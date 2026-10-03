"""领用包（固定组合领用）业务与接口测试。"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.db import connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.core.exceptions import BusinessException
from .models import (
    Goods, KitRequest, KitTemplate, KitVersion, Unit, Category, Variety,
)
from . import services

Q = Decimal


class KitFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("kit-user", "testpass123", role="admin")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.user)}")
        self.unit = Unit.objects.create(name="台", created_by=self.user)
        self.category = Category.objects.create(name="取证设备", unit=self.unit, created_by=self.user)
        self.variety = Variety.objects.create(name="主机", category=self.category, created_by=self.user)

        self.host = self._goods("取证主机", "G-HOST", 10)
        self.cable = self._goods("数据线", "G-CABLE", 10)
        self.disk_a = self._goods("封存硬盘A型号", "G-DISK-A", 5)
        self.disk_b = self._goods("封存硬盘B型号", "G-DISK-B", 3)
        self.case = self._goods("防护箱", "G-CASE", 4)

        self.template = services.create_template("现场取证标准包", "主机+配件+封存介质", self.user)
        self.version = services.create_version(
            self.template,
            items=[
                {"goods": self.host.pk, "quantity": "1", "required": True},
                {"goods": self.cable.pk, "quantity": "2", "required": True},
                {"goods": self.case.pk, "quantity": "1", "required": False},
            ],
            groups=[{
                "name": "封存介质",
                "quantity": "2",
                "required": True,
                "candidates": [
                    {"goods": self.disk_a.pk, "quantity": "2"},
                    {"goods": self.disk_b.pk, "quantity": "2"},
                ],
            }],
            change_remark="初版", actor=self.user, activate=True,
        )

    def _goods(self, name, code, qty):
        return Goods.objects.create(
            variety=self.variety, name=name, code=code,
            quantity=Q(str(qty)), warning_threshold=Q("1"),
        )

    def _select_disk_a(self, reason="A型号与主机接口匹配"):
        return [{
            "goods": self.disk_a.pk, "group": self.version.groups.get(name="封存介质").pk,
            "reason": reason,
        }]


class KitDefinitionTest(KitFixture):
    def test_versioned_template_lifecycle(self):
        self.assertEqual(self.version.status, "active")
        self.assertEqual(self.template.active_version.pk, self.version.pk)

        # 生效版本不可改不可删
        with self.assertRaises(BusinessException):
            services.update_draft_version(
                self.version, "x", [{"goods": self.host.pk, "quantity": "1"}], [], self.user
            )
        with self.assertRaises(BusinessException):
            services.delete_version(self.version, self.user)

        # 升级 V2，旧版本自动归档，已提交申请仍引用旧版本
        request = services.submit_request(
            self.user, self.version.pk, "办案员甲", "一队", "", self._select_disk_a()
        )
        v2 = services.create_version(
            self.template,
            items=[{"goods": self.host.pk, "quantity": "1", "required": True}],
            groups=[], change_remark="精简包", actor=self.user, activate=True,
        )
        self.version.refresh_from_db()
        self.assertEqual(v2.version_no, 2)
        self.assertEqual(v2.status, "active")
        self.assertEqual(self.version.status, "archived")
        self.assertEqual(self.template.active_version.pk, v2.pk)
        request.refresh_from_db()
        self.assertEqual(request.version_id, self.version.pk)

        # 草稿可编辑、可删除
        v3 = services.create_version(
            self.template,
            items=[{"goods": self.host.pk, "quantity": "1", "required": True}],
            groups=[], change_remark="草稿", actor=self.user,
        )
        self.assertEqual(v3.status, "draft")
        services.update_draft_version(
            v3, "改", [{"goods": self.case.pk, "quantity": "1", "required": True}], [], self.user
        )
        services.delete_version(v3, self.user)
        self.assertFalse(KitVersion.objects.filter(pk=v3.pk).exists())

    def test_reject_invalid_payload(self):
        with self.assertRaises(BusinessException):
            # 空包
            services.create_version(self.template, [], [], "", self.user)
        with self.assertRaises(BusinessException):
            # 同物资重复
            services.create_version(
                self.template,
                items=[
                    {"goods": self.host.pk, "quantity": "1"},
                    {"goods": self.host.pk, "quantity": "1"},
                ],
                groups=[], change_remark="", actor=self.user,
            )
        with self.assertRaises(BusinessException):
            # 固定项与候选项重复
            services.create_version(
                self.template,
                items=[{"goods": self.disk_a.pk, "quantity": "1"}],
                groups=[{
                    "name": "介质", "quantity": "1",
                    "candidates": [{"goods": self.disk_a.pk, "quantity": "1"}],
                }],
                change_remark="", actor=self.user,
            )


class KitExpansionTest(KitFixture):
    def test_required_items_auto_expanded(self):
        preview = services.preview_request(self.version.pk, self._select_disk_a())
        self.assertTrue(preview["deliverable"])
        names = {item["goods_name"] for item in preview["items"]}
        # 两个必需固定项 + 选择的可替代介质；未选的可选项不展开
        self.assertEqual(names, {"取证主机", "数据线", "封存硬盘A型号"})

    def test_alternative_requires_selection_and_reason(self):
        group_pk = self.version.groups.get(name="封存介质").pk
        with self.assertRaises(BusinessException):
            services.preview_request(self.version.pk, [])  # 必需分组未选
        with self.assertRaises(BusinessException):
            services.preview_request(
                self.version.pk, [{"goods": self.disk_a.pk, "group": group_pk}]
            )  # 缺选择依据
        with self.assertRaises(BusinessException):
            services.preview_request(
                self.version.pk,
                [{"goods": self.host.pk, "group": group_pk, "reason": "不属于该组"}],
            )  # 非候选项

    def test_optional_fixed_item_selection(self):
        request = services.submit_request(
            self.user, self.version.pk, "办案员甲", "", "",
            self._select_disk_a() + [{"goods": self.case.pk, "quantity": "1"}],
        )
        names = set(request.lines.values_list("goods__name", flat=True))
        self.assertIn("防护箱", names)

    def test_only_active_version_can_submit(self):
        services.create_version(
            self.template,
            items=[{"goods": self.host.pk, "quantity": "1", "required": True}],
            groups=[], change_remark="v2", actor=self.user, activate=True,
        )
        self.version.refresh_from_db()
        with self.assertRaises(BusinessException):
            services.submit_request(
                self.user, self.version.pk, "甲", "", "", self._select_disk_a()
            )


class KitOccupancyFlowTest(KitFixture):
    def test_submit_occupies_all_or_nothing(self):
        # 硬盘B库存3但需要每包2；先发一包占掉A型号2
        services.submit_request(
            self.user, self.version.pk, "甲", "", "",
            [{"goods": self.disk_b.pk,
              "group": self.version.groups.get(name="封存介质").pk,
              "reason": "B更稳定"}],
        )
        self.disk_b.refresh_from_db()
        self.assertEqual(self.disk_b.occupied_quantity, Q("2"))
        self.assertEqual(self.disk_b.available_quantity, Q("1"))

        # A型号库存5：两包占4后，第三包必需项缺口 → 整单拒绝且不留任何占用
        for _ in range(2):
            services.submit_request(
                self.user, self.version.pk, "甲", "", "", self._select_disk_a()
            )
        self.host.refresh_from_db()
        self.cable.refresh_from_db()
        self.disk_a.refresh_from_db()
        before_host = self.host.occupied_quantity
        before_cable = self.cable.occupied_quantity
        before_disk = self.disk_a.occupied_quantity
        with self.assertRaises(BusinessException):
            services.submit_request(
                self.user, self.version.pk, "甲", "", "", self._select_disk_a()
            )
        self.host.refresh_from_db()
        self.cable.refresh_from_db()
        self.disk_a.refresh_from_db()
        self.assertEqual(self.host.occupied_quantity, before_host)
        self.assertEqual(self.cable.occupied_quantity, before_cable)
        self.assertEqual(self.disk_a.occupied_quantity, before_disk)
        self.assertEqual(KitRequest.objects.count(), 3)

    def test_alternative_reason_persisted(self):
        request = services.submit_request(
            self.user, self.version.pk, "甲", "", "",
            [{"goods": self.disk_b.pk,
              "group": self.version.groups.get(name="封存介质").pk,
              "reason": "现场仅B型号可用"}],
        )
        line = request.lines.get(is_alternative=True)
        self.assertTrue(line.is_alternative)
        self.assertEqual(line.selection_reason, "现场仅B型号可用")
        self.assertEqual(line.group_name, "封存介质")

    def test_reject_releases_occupancy(self):
        request = services.submit_request(
            self.user, self.version.pk, "甲", "", "", self._select_disk_a()
        )
        services.approve_request(request.pk, self.user, approved=False, opinion="手续不全")
        request.refresh_from_db()
        self.assertEqual(request.status, "rejected")
        for goods in (self.host, self.cable, self.disk_a):
            goods.refresh_from_db()
            self.assertEqual(goods.occupied_quantity, Q("0"))
            self.assertEqual(goods.available_quantity, goods.quantity)
        self.assertTrue(request.lines.filter(status="released").exists())

    def test_issue_and_partial_then_full_return(self):
        request = services.submit_request(
            self.user, self.version.pk, "甲", "", "", self._select_disk_a()
        )
        services.approve_request(request.pk, self.user, approved=True)
        services.issue_request(request.pk, self.user)
        request.refresh_from_db()
        self.assertEqual(request.status, "issued")

        self.host.refresh_from_db()
        self.cable.refresh_from_db()
        self.disk_a.refresh_from_db()
        self.assertEqual(self.host.quantity, Q("9"))
        self.assertEqual(self.host.occupied_quantity, Q("0"))
        self.assertEqual(self.cable.quantity, Q("8"))
        self.assertEqual(self.disk_a.quantity, Q("3"))

        # 部分退回：2根数据线只退1根
        cable_line = request.lines.get(goods=self.cable)
        services.return_request(request.pk, self.user, [
            {"line": cable_line.pk, "quantity": "1", "reason": "一根损坏待鉴定"}
        ])
        request.refresh_from_db()
        cable_line.refresh_from_db()
        self.assertEqual(request.status, "partial_returned")
        self.assertEqual(cable_line.status, "partial_returned")
        self.assertEqual(cable_line.returned_quantity, Q("1"))
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.quantity, Q("9"))

        # 超过可退数量必须拒绝
        with self.assertRaises(BusinessException):
            services.return_request(request.pk, self.user, [
                {"line": cable_line.pk, "quantity": "2"}
            ])

        # 全部退回
        rest = [
            {"line": line.pk, "quantity": str(line.issued_quantity - line.returned_quantity)}
            for line in request.lines.all()
            if line.issued_quantity - line.returned_quantity > 0
        ]
        services.return_request(request.pk, self.user, rest)
        request.refresh_from_db()
        self.assertEqual(request.status, "returned")
        self.assertTrue(all(l.status == "returned" for l in request.lines.all()))
        self.host.refresh_from_db()
        self.assertEqual(self.host.quantity, Q("10"))
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.quantity, Q("10"))
        self.disk_a.refresh_from_db()
        self.assertEqual(self.disk_a.quantity, Q("5"))

    def test_cancel_releases_occupancy(self):
        request = services.submit_request(
            self.user, self.version.pk, "甲", "", "", self._select_disk_a()
        )
        services.cancel_request(request.pk, self.user)
        request.refresh_from_db()
        self.assertEqual(request.status, "cancelled")
        self.disk_a.refresh_from_db()
        self.assertEqual(self.disk_a.occupied_quantity, Q("0"))

    def test_freeze_blocks_new_occupancy(self):
        # 冻结硬盘A的全部5件，申请无法交付
        services.freeze_goods(self.disk_a.pk, self.user, Q("5"), "送检鉴定")
        self.disk_a.refresh_from_db()
        self.assertEqual(self.disk_a.available_quantity, Q("0"))
        with self.assertRaises(BusinessException):
            services.submit_request(
                self.user, self.version.pk, "甲", "", "", self._select_disk_a()
            )
        # 解冻后可正常申请
        services.unfreeze_goods(self.disk_a.pk, self.user, Q("5"), "鉴定完毕")
        request = services.submit_request(
            self.user, self.version.pk, "甲", "", "", self._select_disk_a()
        )
        self.assertEqual(request.status, "pending")

        # 已占用部分不可再冻结
        with self.assertRaises(BusinessException):
            services.freeze_goods(self.disk_a.pk, self.user, Q("4"), "再次冻结")

    def test_freeze_over_quantity_rejected(self):
        with self.assertRaises(BusinessException):
            services.freeze_goods(self.host.pk, self.user, Q("11"), "超量")
        services.freeze_goods(self.host.pk, self.user, Q("3"), "抽检")
        with self.assertRaises(BusinessException):
            services.unfreeze_goods(self.host.pk, self.user, Q("4"), "超额解冻")


class KitConcurrencyTest(TransactionTestCase):
    """两个申请并发争用同一配件：只能有一个成功，库存绝不超占。"""

    def setUp(self):
        self.user = User.objects.create_user("kit-conc", "testpass123", role="admin")
        unit = Unit.objects.create(name="个", created_by=self.user)
        category = Category.objects.create(name="配件", unit=unit, created_by=self.user)
        variety = Variety.objects.create(name="稀缺配件", category=category, created_by=self.user)
        self.goods = Goods.objects.create(
            variety=variety, name="唯一采集卡", code="G-RARE",
            quantity=Q("10"), warning_threshold=Q("1"),
        )
        template = services.create_template("单配件包", "", self.user)
        self.version = services.create_version(
            template,
            items=[{"goods": self.goods.pk, "quantity": "8", "required": True}],
            groups=[], change_remark="", actor=self.user, activate=True,
        )

    def _submit(self):
        try:
            services.submit_request(
                self.user, self.version.pk, "并发申请人", "", "", []
            )
            return "ok"
        except Exception as exc:  # 业务冲突或 SQLite 写锁均视为落败
            return f"{type(exc).__name__}: {exc}"
        finally:
            connections.close_all()

    def test_parallel_requests_never_oversubscribe(self):
        # 引擎以 BEGIN IMMEDIATE 串行化写事务；落败方等待后由条件更新干净地拒绝。
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self._submit(), range(2)))

        self.assertEqual([r for r in results if r.startswith("ok")].count("ok"), 1, results)
        losers = [r for r in results if not r.startswith("ok")]
        self.assertTrue(losers and losers[0].startswith("BusinessException"), results)
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.occupied_quantity, Q("8"))
        self.assertEqual(self.goods.available_quantity, Q("2"))
        self.assertEqual(KitRequest.objects.filter(status="pending").count(), 1)


class KitAPITest(KitFixture):
    def test_full_flow_api(self):
        # 版本详情
        r = self.client.get(f"/api/kit-versions/{self.version.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["version_no"], 1)

        # 预览可交付
        group_pk = self.version.groups.get(name="封存介质").pk
        r = self.client.post("/api/kit-requests/preview/", {
            "version": self.version.pk,
            "selections": [{"goods": self.disk_a.pk, "group": group_pk,
                            "reason": "接口匹配"}],
        }, format="json")
        self.assertEqual(r.status_code, 200, r.json())
        self.assertTrue(r.json()["data"]["deliverable"])

        # 提交申请
        r = self.client.post("/api/kit-requests/", {
            "version": self.version.pk, "receiver": "办案员乙",
            "selections": [{"goods": self.disk_a.pk, "group": group_pk,
                            "reason": "接口匹配"}],
        }, format="json")
        self.assertEqual(r.status_code, 200, r.json())
        req_id = r.json()["data"]["id"]
        self.assertTrue(r.json()["data"]["number"].startswith("KR"))
        self.assertEqual(len(r.json()["data"]["lines"]), 3)

        # 冻结接口
        r = self.client.post("/api/stock-freeze/freeze/", {
            "goods": self.case.pk, "quantity": "4", "reason": "抽检封存"
        }, format="json")
        self.assertEqual(r.status_code, 200, r.json())

        # 审批 → 发放 → 退回
        r = self.client.post(f"/api/kit-requests/{req_id}/approve/",
                             {"approved": True}, format="json")
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/api/kit-requests/{req_id}/issue/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.json())
        line_id = r.json()["data"]["lines"][0]["id"]
        r = self.client.post(f"/api/kit-requests/{req_id}/return/", {
            "items": [{"line": line_id, "quantity": "1"}]
        }, format="json")
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(r.json()["data"]["status"], "partial_returned")

    def test_shortage_preview_api(self):
        # 冻结全部 A/B 硬盘后预览不可交付
        services.freeze_goods(self.disk_a.pk, self.user, Q("5"), "送检")
        services.freeze_goods(self.disk_b.pk, self.user, Q("3"), "送检")
        group_pk = self.version.groups.get(name="封存介质").pk
        r = self.client.post("/api/kit-requests/preview/", {
            "version": self.version.pk,
            "selections": [{"goods": self.disk_a.pk, "group": group_pk,
                            "reason": "x"}],
        }, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["data"]["deliverable"])

        # 提交必须被拒绝
        r = self.client.post("/api/kit-requests/", {
            "version": self.version.pk, "receiver": "甲",
            "selections": [{"goods": self.disk_a.pk, "group": group_pk,
                            "reason": "x"}],
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(KitRequest.objects.count(), 0)

    def test_template_crud_api(self):
        r = self.client.post("/api/kits/", {"name": "新增包", "description": "d"}, format="json")
        self.assertEqual(r.status_code, 200, r.json())
        pk = r.json()["data"]["id"]
        r = self.client.post(f"/api/kits/{pk}/versions/", {
            "items": [{"goods": self.host.pk, "quantity": "1"}],
            "groups": [], "activate": True,
        }, format="json")
        self.assertEqual(r.status_code, 200, r.json())
        version_pk = r.json()["data"]["id"]
        r = self.client.post(f"/api/kit-versions/{version_pk}/activate/", {}, format="json")
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/kits/")
        self.assertEqual(r.json()["data"]["total"], 2)


class KitPermissionTest(KitFixture):
    def setUp(self):
        super().setUp()
        self.normal = User.objects.create_user("normal-user", "testpass123", role="user")
        self.normal_client = APIClient()
        self.normal_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {generate_token(self.normal)}"
        )

    def test_role_boundaries(self):
        group_pk = self.version.groups.get(name="封存介质").pk
        selection = [{"goods": self.disk_a.pk, "group": group_pk, "reason": "接口匹配"}]

        # 普通用户不能维护包定义
        r = self.normal_client.post("/api/kits/", {"name": "越权包"}, format="json")
        self.assertEqual(r.status_code, 403)

        # 普通用户可以提交领用申请
        r = self.normal_client.post("/api/kit-requests/", {
            "version": self.version.pk, "receiver": "本人",
            "selections": selection,
        }, format="json")
        self.assertEqual(r.status_code, 200, r.json())
        req_id = r.json()["data"]["id"]

        # 普通用户不能审批、冻结
        r = self.normal_client.post(f"/api/kit-requests/{req_id}/approve/",
                                    {"approved": True}, format="json")
        self.assertEqual(r.status_code, 403)
        r = self.normal_client.post("/api/stock-freeze/freeze/", {
            "goods": self.host.pk, "quantity": "1", "reason": "越权冻结"
        }, format="json")
        self.assertEqual(r.status_code, 403)

        # 普通用户可以撤回自己的申请
        r = self.normal_client.post(f"/api/kit-requests/{req_id}/cancel/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.json())
