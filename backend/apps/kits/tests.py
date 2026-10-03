"""
领用包测试

覆盖：有版本的包定义维护、申请展开为物资占用、必需项审批门禁、
替代项选择依据，以及部分退回、包定义升级、货物冻结、并发争用
场景下数量与包状态的一致性。
"""
import threading
from decimal import Decimal

from django.db import connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.warehouse.models import Category, Goods, Unit, Variety
from . import services
from .models import Kit, KitApplication, KitApplicationItem, KitItem, KitVersion


class KitFixture(TestCase):
    """基础数据：主机/配件/封存介质组合的标准套装"""

    def setUp(self):
        self.user = User.objects.create_user("kit-user", "testpass123", role="admin")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.user)}")

        self.unit = Unit.objects.create(name="台", created_by=self.user)
        self.category = Category.objects.create(name="受控器材", unit=self.unit, created_by=self.user)
        self.variety = Variety.objects.create(name="勘查设备", category=self.category, created_by=self.user)

        self.host = self._goods("勘查主机", "HOST-001", "5")
        self.accessory = self._goods("取证配件", "ACC-001", "2")
        self.media = self._goods("封存硬盘", "MED-001", "3")
        self.alt_media = self._goods("备用封存硬盘", "MED-002", "4")
        self.printer = self._goods("便携打印机", "PRT-001", "1")

        self.kit = Kit.objects.create(name="现场勘查套装", code="KIT-XC", created_by=self.user)
        self.version = KitVersion.objects.create(kit=self.kit, version_no=1, created_by=self.user)
        self.item_host = self._item(self.host, "host", "1")
        self.item_accessory = self._item(self.accessory, "accessory", "2")
        self.item_media = self._item(self.media, "media", "1", subs=[self.alt_media])
        self.item_printer = self._item(self.printer, "accessory", "1", required=False)
        services.publish_version(self.version)
        self.version.refresh_from_db()

    def _goods(self, name, code, quantity):
        return Goods.objects.create(
            variety=self.variety, name=name, code=code, quantity=Decimal(quantity)
        )

    def _item(self, goods, role, quantity, required=True, subs=()):
        item = KitItem.objects.create(
            version=self.version, goods=goods, role=role,
            quantity=Decimal(quantity), required=required,
        )
        if subs:
            item.substitutes.set(subs)
        return item

    def _apply(self, **payload):
        data = {"kit": self.kit.id, "receiver": "办案人甲"}
        data.update(payload)
        return self.client.post("/api/kit-applications/", data, format="json")

    def _set_stock(self, goods, quantity):
        Goods.objects.filter(pk=goods.pk).update(quantity=Decimal(quantity))
        goods.refresh_from_db()


class KitDefinitionTest(KitFixture):
    def test_create_kit_with_items_creates_draft_version(self):
        response = self.client.post("/api/kits/", {
            "name": "讯问记录套装",
            "code": "KIT-XW",
            "description": "讯问室固定组合",
            "items": [
                {"goods": self.host.id, "role": "host", "quantity": "1"},
                {"goods": self.media.id, "role": "media", "quantity": "2",
                 "substitutes": [self.alt_media.id]},
            ],
        }, format="json")
        self.assertEqual(response.status_code, 200)
        kit = Kit.objects.get(code="KIT-XW")
        self.assertIsNone(kit.current_version)
        version = kit.versions.get()
        self.assertEqual(version.status, "draft")
        self.assertEqual(version.items.count(), 2)

    def test_duplicate_kit_name_rejected(self):
        response = self.client.post("/api/kits/", {
            "name": "现场勘查套装", "code": "KIT-NEW",
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_publish_and_current_version(self):
        response = self.client.post("/api/kits/", {
            "name": "移动取证套装", "code": "KIT-YD",
            "items": [{"goods": self.host.id, "role": "host", "quantity": "1"}],
        }, format="json")
        kit = Kit.objects.get(code="KIT-YD")
        version = kit.versions.get()
        published = self.client.post(f"/api/kit-versions/{version.id}/publish/")
        self.assertEqual(published.status_code, 200)
        kit.refresh_from_db()
        self.assertEqual(kit.current_version.id, version.id)

    def test_publish_empty_version_rejected(self):
        response = self.client.post("/api/kits/", {"name": "空套装", "code": "KIT-EMPTY"}, format="json")
        kit = Kit.objects.get(code="KIT-EMPTY")
        created = self.client.post(f"/api/kits/{kit.id}/versions/", {}, format="json")
        self.assertEqual(created.status_code, 200)
        version = kit.versions.get()
        published = self.client.post(f"/api/kit-versions/{version.id}/publish/")
        self.assertEqual(published.status_code, 400)

    def test_published_version_is_immutable(self):
        changed = self.client.put(f"/api/kit-versions/{self.version.id}/", {
            "items": [{"goods": self.host.id, "role": "host", "quantity": "9"}],
        }, format="json")
        deleted = self.client.delete(f"/api/kit-versions/{self.version.id}/")
        self.assertEqual(changed.status_code, 400)
        self.assertEqual(deleted.status_code, 400)
        self.assertEqual(self.version.items.count(), 4)

    def test_upgrade_flow_copies_and_retires(self):
        # 升级：基于当前版本复制出新草稿
        created = self.client.post(f"/api/kits/{self.kit.id}/versions/", {"note": "v2"}, format="json")
        self.assertEqual(created.status_code, 200)
        v2_id = created.json()["data"]["id"]
        self.assertEqual(created.json()["data"]["version_no"], 2)
        self.assertEqual(len(created.json()["data"]["items"]), 4)

        # 草稿可改：配件数量从 2 调整为 1
        updated = self.client.put(f"/api/kit-versions/{v2_id}/", {
            "items": [
                {"goods": self.host.id, "role": "host", "quantity": "1"},
                {"goods": self.accessory.id, "role": "accessory", "quantity": "1"},
                {"goods": self.media.id, "role": "media", "quantity": "1",
                 "substitutes": [self.alt_media.id]},
            ],
        }, format="json")
        self.assertEqual(updated.status_code, 200)

        published = self.client.post(f"/api/kit-versions/{v2_id}/publish/")
        self.assertEqual(published.status_code, 200)
        self.version.refresh_from_db()
        self.kit.refresh_from_db()
        self.assertEqual(self.version.status, "retired")
        self.assertEqual(self.kit.current_version.id, v2_id)

    def test_delete_kit_with_applications_rejected(self):
        self._apply()
        response = self.client.delete(f"/api/kits/{self.kit.id}/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Kit.objects.filter(pk=self.kit.id).exists())

    def test_requires_authentication(self):
        anonymous = APIClient().get("/api/kits/")
        self.assertEqual(anonymous.status_code, 401)


class KitApplicationTest(KitFixture):
    def test_application_expands_to_reservations(self):
        response = self._apply(receiver_dept="刑侦一队")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["status"], "pending")
        self.assertEqual(len(data["items"]), 4)

        self.host.refresh_from_db()
        self.accessory.refresh_from_db()
        self.media.refresh_from_db()
        self.printer.refresh_from_db()
        self.assertEqual(self.host.reserved_quantity, Decimal("1"))
        self.assertEqual(self.accessory.reserved_quantity, Decimal("2"))
        self.assertEqual(self.media.reserved_quantity, Decimal("1"))
        self.assertEqual(self.printer.reserved_quantity, Decimal("1"))

        # 明细快照保留组合角色与必需标记
        items = {item["goods"]: item for item in data["items"]}
        self.assertEqual(items[self.host.id]["role"], "host")
        self.assertTrue(items[self.accessory.id]["required"])
        self.assertFalse(items[self.printer.id]["required"])

    def test_required_shortage_blocks_approval(self):
        self._set_stock(self.accessory, "1")  # 每套需要 2
        response = self._apply()
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["status"], "shortage")

        # 缺货明细说明整套能否交付
        blocking = [d for d in data["shortage_detail"] if d["blocking"]]
        self.assertEqual(len(blocking), 1)
        self.assertEqual(blocking[0]["goods_id"], self.accessory.id)
        self.assertEqual(blocking[0]["requested"], "2.00")
        self.assertEqual(blocking[0]["available"], "1.00")

        # 整单回滚：任何货物都未被占用
        for goods in (self.host, self.accessory, self.media, self.printer):
            goods.refresh_from_db()
            self.assertEqual(goods.reserved_quantity, Decimal("0"))

        # 缺货申请不能进入审批
        approved = self.client.post(f"/api/kit-applications/{data['id']}/approve/")
        self.assertEqual(approved.status_code, 400)

    def test_optional_shortage_still_enters_approval(self):
        self._set_stock(self.printer, "0")
        response = self._apply()
        data = response.json()["data"]
        self.assertEqual(data["status"], "pending")
        optional = [d for d in data["shortage_detail"] if not d["blocking"]]
        self.assertEqual(len(optional), 1)
        self.assertEqual(optional[0]["goods_id"], self.printer.id)

        approved = self.client.post(f"/api/kit-applications/{data['id']}/approve/")
        self.assertEqual(approved.status_code, 200)

    def test_substitute_requires_reason(self):
        response = self._apply(substitute_choices=[
            {"kit_item": self.item_media.id, "substitute_goods": self.alt_media.id, "reason": ""},
        ])
        self.assertEqual(response.status_code, 400)

    def test_substitute_choice_recorded_and_reserved(self):
        response = self._apply(substitute_choices=[
            {"kit_item": self.item_media.id, "substitute_goods": self.alt_media.id,
             "reason": "主选封存硬盘规格不符，改用同容量备用盘"},
        ])
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["status"], "pending")

        self.media.refresh_from_db()
        self.alt_media.refresh_from_db()
        self.assertEqual(self.media.reserved_quantity, Decimal("0"))
        self.assertEqual(self.alt_media.reserved_quantity, Decimal("1"))

        substitute_item = [i for i in data["items"] if i["goods"] == self.alt_media.id][0]
        self.assertTrue(substitute_item["is_substitute"])
        self.assertEqual(substitute_item["substitute_reason"], "主选封存硬盘规格不符，改用同容量备用盘")

    def test_invalid_substitute_rejected(self):
        response = self._apply(substitute_choices=[
            {"kit_item": self.item_media.id, "substitute_goods": self.host.id, "reason": "随意替换"},
        ])
        self.assertEqual(response.status_code, 400)

    def test_frozen_goods_blocks_application_until_recheck(self):
        self.client.post(f"/api/goods/{self.accessory.id}/freeze/", {"reason": "涉案暂扣"}, format="json")
        response = self._apply()
        data = response.json()["data"]
        self.assertEqual(data["status"], "shortage")
        frozen_entries = [d for d in data["shortage_detail"] if d["frozen"]]
        self.assertEqual(len(frozen_entries), 1)
        self.assertEqual(frozen_entries[0]["goods_id"], self.accessory.id)

        # 解冻后重新校验，申请进入审批
        self.client.post(f"/api/goods/{self.accessory.id}/unfreeze/")
        rechecked = self.client.post(f"/api/kit-applications/{data['id']}/recheck/", {}, format="json")
        self.assertEqual(rechecked.status_code, 200)
        self.assertEqual(rechecked.json()["data"]["status"], "pending")
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.reserved_quantity, Decimal("2"))

    def test_frozen_goods_blocks_issue_but_not_quantities(self):
        application_id = self._apply().json()["data"]["id"]
        self.client.post(f"/api/kit-applications/{application_id}/approve/")
        self.client.post(f"/api/goods/{self.host.id}/freeze/", {"reason": "盘点封存"}, format="json")

        issued = self.client.post(f"/api/kit-applications/{application_id}/issue/")
        self.assertEqual(issued.status_code, 400)
        self.host.refresh_from_db()
        self.assertEqual(self.host.quantity, Decimal("5"))
        self.assertEqual(self.host.reserved_quantity, Decimal("1"))

        self.client.post(f"/api/goods/{self.host.id}/unfreeze/")
        issued = self.client.post(f"/api/kit-applications/{application_id}/issue/")
        self.assertEqual(issued.status_code, 200)
        self.host.refresh_from_db()
        self.assertEqual(self.host.quantity, Decimal("4"))
        self.assertEqual(self.host.reserved_quantity, Decimal("0"))

    def test_reject_releases_reservation(self):
        application_id = self._apply().json()["data"]["id"]
        rejected = self.client.post(f"/api/kit-applications/{application_id}/reject/",
                                    {"remark": "审批不通过"}, format="json")
        self.assertEqual(rejected.status_code, 200)
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.reserved_quantity, Decimal("0"))
        item = KitApplicationItem.objects.get(application_id=application_id, goods=self.accessory)
        self.assertEqual(item.status, "released")
        self.assertEqual(item.reserved_quantity, Decimal("0"))

    def test_cancel_releases_reservation(self):
        application_id = self._apply().json()["data"]["id"]
        cancelled = self.client.post(f"/api/kit-applications/{application_id}/cancel/")
        self.assertEqual(cancelled.status_code, 200)
        self.host.refresh_from_db()
        self.assertEqual(self.host.reserved_quantity, Decimal("0"))

    def test_issue_deducts_stock(self):
        application_id = self._apply().json()["data"]["id"]
        self.client.post(f"/api/kit-applications/{application_id}/approve/")
        issued = self.client.post(f"/api/kit-applications/{application_id}/issue/")
        self.assertEqual(issued.status_code, 200)
        self.assertEqual(issued.json()["data"]["status"], "issued")

        self.host.refresh_from_db()
        self.accessory.refresh_from_db()
        self.assertEqual(self.host.quantity, Decimal("4"))
        self.assertEqual(self.host.reserved_quantity, Decimal("0"))
        self.assertEqual(self.accessory.quantity, Decimal("0"))
        self.assertEqual(self.accessory.reserved_quantity, Decimal("0"))

        item = KitApplicationItem.objects.get(application_id=application_id, goods=self.accessory)
        self.assertEqual(item.issued_quantity, Decimal("2"))
        self.assertEqual(item.status, "issued")

    def test_partial_return_keeps_consistency(self):
        application_id = self._apply().json()["data"]["id"]
        self.client.post(f"/api/kit-applications/{application_id}/approve/")
        self.client.post(f"/api/kit-applications/{application_id}/issue/")
        accessory_item = KitApplicationItem.objects.get(
            application_id=application_id, goods=self.accessory
        )

        # 先退 1 件配件：部分退回
        returned = self.client.post(f"/api/kit-applications/{application_id}/return/", {
            "items": [{"item": accessory_item.id, "quantity": "1"}],
        }, format="json")
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(returned.json()["data"]["status"], "partially_returned")
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.quantity, Decimal("1"))
        accessory_item.refresh_from_db()
        self.assertEqual(accessory_item.returned_quantity, Decimal("1"))
        self.assertEqual(accessory_item.status, "partially_returned")

        # 再退剩余 1 件：全部退回
        returned = self.client.post(f"/api/kit-applications/{application_id}/return/", {
            "items": [{"item": accessory_item.id, "quantity": "1"}],
        }, format="json")
        self.assertEqual(returned.status_code, 200)
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.quantity, Decimal("2"))
        accessory_item.refresh_from_db()
        self.assertEqual(accessory_item.status, "returned")

        # 其余明细全部退回后整单才为已退回
        application = KitApplication.objects.get(pk=application_id)
        self.assertEqual(application.status, "partially_returned")
        for item in application.items.exclude(pk=accessory_item.pk):
            self.client.post(f"/api/kit-applications/{application_id}/return/", {
                "items": [{"item": item.id, "quantity": str(item.issued_quantity)}],
            }, format="json")
        application.refresh_from_db()
        self.assertEqual(application.status, "returned")

    def test_return_exceeding_issued_rejected(self):
        application_id = self._apply().json()["data"]["id"]
        self.client.post(f"/api/kit-applications/{application_id}/approve/")
        self.client.post(f"/api/kit-applications/{application_id}/issue/")
        accessory_item = KitApplicationItem.objects.get(
            application_id=application_id, goods=self.accessory
        )

        response = self.client.post(f"/api/kit-applications/{application_id}/return/", {
            "items": [{"item": accessory_item.id, "quantity": "5"}],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.quantity, Decimal("0"))
        accessory_item.refresh_from_db()
        self.assertEqual(accessory_item.returned_quantity, Decimal("0"))

    def test_upgrade_keeps_existing_application_pinned(self):
        self._set_stock(self.accessory, "5")
        old_application_id = self._apply().json()["data"]["id"]

        # 包定义升级：v2 配件改为 1 件
        created = self.client.post(f"/api/kits/{self.kit.id}/versions/", {
            "items": [
                {"goods": self.host.id, "role": "host", "quantity": "1"},
                {"goods": self.accessory.id, "role": "accessory", "quantity": "1"},
                {"goods": self.media.id, "role": "media", "quantity": "1"},
            ],
        }, format="json")
        v2_id = created.json()["data"]["id"]
        self.client.post(f"/api/kit-versions/{v2_id}/publish/")

        # 旧申请仍锁定 v1：占用与数量不变
        old_application = KitApplication.objects.get(pk=old_application_id)
        self.assertEqual(old_application.kit_version.version_no, 1)
        old_item = old_application.items.get(goods=self.accessory)
        self.assertEqual(old_item.requested_quantity, Decimal("2"))
        self.assertEqual(old_item.reserved_quantity, Decimal("2"))

        # 新申请按 v2 展开
        new_application_id = self._apply(receiver="办案人乙").json()["data"]["id"]
        new_application = KitApplication.objects.get(pk=new_application_id)
        self.assertEqual(new_application.kit_version.version_no, 2)
        new_item = new_application.items.get(goods=self.accessory)
        self.assertEqual(new_item.requested_quantity, Decimal("1"))

        # 旧申请仍可按 v1 数量正常发放
        self.client.post(f"/api/kit-applications/{old_application_id}/approve/")
        issued = self.client.post(f"/api/kit-applications/{old_application_id}/issue/")
        self.assertEqual(issued.status_code, 200)
        self.accessory.refresh_from_db()
        # 5 - 2(旧申请发放) = 3；新申请的 1 件仍处占用状态未扣减
        self.assertEqual(self.accessory.quantity, Decimal("3"))
        self.assertEqual(self.accessory.reserved_quantity, Decimal("1"))

    def test_recheck_after_restock(self):
        self._set_stock(self.accessory, "1")
        application_id = self._apply().json()["data"]["id"]
        application = KitApplication.objects.get(pk=application_id)
        self.assertEqual(application.status, "shortage")

        self._set_stock(self.accessory, "3")
        rechecked = self.client.post(f"/api/kit-applications/{application_id}/recheck/", {}, format="json")
        self.assertEqual(rechecked.json()["data"]["status"], "pending")
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.reserved_quantity, Decimal("2"))

    def test_sequential_contention_never_over_allocates(self):
        # 配件库存 2，仅够一套；两个申请先后到达，后者缺货
        first = self._apply(receiver="办案人甲")
        second = self._apply(receiver="办案人乙")
        self.assertEqual(first.json()["data"]["status"], "pending")
        self.assertEqual(second.json()["data"]["status"], "shortage")
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.reserved_quantity, Decimal("2"))
        self.assertEqual(KitApplication.objects.filter(status="pending").count(), 1)


class KitConcurrencyTest(TransactionTestCase):
    """并发争用：两个申请同时抢同一配件，占用数量不能超发"""

    def setUp(self):
        self.user = User.objects.create_user("kit-race-user", "testpass123", role="admin")
        unit = Unit.objects.create(name="台", created_by=self.user)
        category = Category.objects.create(name="受控器材", unit=unit, created_by=self.user)
        variety = Variety.objects.create(name="勘查设备", category=category, created_by=self.user)
        self.accessory = Goods.objects.create(
            variety=variety, name="取证配件", code="ACC-RACE", quantity=Decimal("2")
        )
        self.kit = Kit.objects.create(name="并发测试套装", code="KIT-RACE", created_by=self.user)
        version = KitVersion.objects.create(kit=self.kit, version_no=1, created_by=self.user)
        KitItem.objects.create(
            version=version, goods=self.accessory, role="accessory",
            quantity=Decimal("2"), required=True,
        )
        services.publish_version(version)

    def test_two_applications_race_for_same_accessory(self):
        barrier = threading.Barrier(2)
        outcomes = {}

        def worker(key):
            try:
                barrier.wait(timeout=10)
                application = services.create_application(
                    user=self.user, kit=self.kit, receiver=f"办案人{key}",
                    receiver_dept="", remark="", substitute_choices=[],
                )
                outcomes[key] = application.status
            except Exception as exc:  # noqa: BLE001 - 测试需捕获线程内异常
                outcomes[key] = f"error: {exc}"
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(key,)) for key in ("甲", "乙")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(
            sorted(outcomes.values()), ["pending", "shortage"],
            f"并发结果异常: {outcomes}",
        )
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.reserved_quantity, Decimal("2"))
        self.assertEqual(KitApplication.objects.filter(status="pending").count(), 1)
        self.assertEqual(KitApplicationItem.objects.count(), 1)
