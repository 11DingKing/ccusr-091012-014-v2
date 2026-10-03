"""
领用包业务服务

所有库存数量变更都通过带守卫条件的单条原子 UPDATE 完成，
保证部分退回、包定义升级、货物冻结、并发争用等场景下
数量与包状态始终一致。
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Case, F, Value, When
from django.utils import timezone

from apps.core.exceptions import BusinessException
from apps.warehouse.models import Goods
from .models import KitApplication, KitApplicationItem


class RequiredItemShortage(Exception):
    """必需项缺货（内部控制流异常，触发整单回滚）"""


@dataclass
class PlanEntry:
    """占用计划项"""
    item: object          # KitItem
    goods_id: int         # 实际占用的货物（可能是替代物资）
    quantity: Decimal
    is_substitute: bool
    reason: str


# ==================== 原子库存操作 ====================

def reserve_goods(goods_id, quantity):
    """原子占用库存：仅在货物启用、未冻结且可用量充足时成功"""
    updated = Goods.objects.filter(
        pk=goods_id,
        is_active=True,
        is_frozen=False,
        quantity__gte=F('reserved_quantity') + quantity,
    ).update(reserved_quantity=F('reserved_quantity') + quantity)
    return updated == 1


def release_goods(goods_id, quantity):
    """原子释放占用，占用量不会降为负数"""
    updated = Goods.objects.filter(
        pk=goods_id,
        reserved_quantity__gte=quantity,
    ).update(reserved_quantity=F('reserved_quantity') - quantity)
    if updated != 1:
        raise BusinessException('占用数量不足，无法释放')


def deduct_reserved_goods(goods_id, quantity):
    """发放扣减：实际库存与占用同步减少，冻结货物拒绝发放"""
    updated = Goods.objects.filter(
        pk=goods_id,
        is_frozen=False,
        reserved_quantity__gte=quantity,
    ).update(
        quantity=F('quantity') - quantity,
        reserved_quantity=F('reserved_quantity') - quantity,
    )
    if updated != 1:
        goods = Goods.objects.filter(pk=goods_id).first()
        if goods and goods.is_frozen:
            raise BusinessException(f'货物"{goods.name}"已冻结，无法发放')
        raise BusinessException('占用数量不足，无法发放')


def restock_goods(goods_id, quantity):
    """退回入库：实际库存增加"""
    Goods.objects.filter(pk=goods_id).update(quantity=F('quantity') + quantity)


# ==================== 版本维护 ====================

def replace_version_items(version, items_data):
    """整体替换草稿版本的物资项（已发布版本不可修改）"""
    if version.status != 'draft':
        raise BusinessException('仅草稿版本可修改物资项')
    with transaction.atomic():
        version.items.all().delete()
        for data in items_data:
            item = version.items.create(
                goods_id=data['goods'],
                role=data['role'],
                quantity=data['quantity'],
                required=data['required'],
                sort_order=data['sort_order'],
            )
            item.substitutes.set(data['substitutes'])
    return version


def publish_version(version):
    """发布版本：同包其他已发布版本自动停用，历史申请仍锁定原版本"""
    if version.status != 'draft':
        raise BusinessException('仅草稿版本可发布')
    if not version.items.exists():
        raise BusinessException('版本未定义物资项，无法发布')
    with transaction.atomic():
        version.kit.versions.filter(status='published').update(status='retired')
        version.status = 'published'
        version.published_at = timezone.now()
        version.save(update_fields=['status', 'published_at', 'updated_at'])
    return version


# ==================== 申请展开与占用 ====================

def _build_plan(items, substitute_choices):
    """校验替代选择并生成占用计划，替代物资必须属于定义的可替代范围且记录选择依据"""
    items_by_id = {item.id: item for item in items}
    choice_map = {}
    for choice in substitute_choices:
        item = items_by_id.get(choice['kit_item'])
        if item is None:
            raise BusinessException('替代选择包含不属于该版本的物资项')
        if item.id in choice_map:
            raise BusinessException(f'物资项"{item.goods.name}"存在重复替代选择')
        substitute_goods_id = choice['substitute_goods']
        if not item.substitutes.filter(pk=substitute_goods_id).exists():
            raise BusinessException(f'物资项"{item.goods.name}"不支持该替代物资')
        reason = choice['reason'].strip()
        if not reason:
            raise BusinessException('选择替代物资必须记录选择依据')
        choice_map[item.id] = (substitute_goods_id, reason)

    plan = []
    for item in items:
        if item.id in choice_map:
            goods_id, reason = choice_map[item.id]
            plan.append(PlanEntry(item, goods_id, item.quantity, True, reason))
        else:
            plan.append(PlanEntry(item, item.goods_id, item.quantity, False, ''))
    return plan


def _shortage_dict(entry, blocking):
    """生成缺货说明（数值转字符串以便 JSON 存储）"""
    goods = Goods.objects.filter(pk=entry.goods_id).first()
    available = Decimal('0')
    frozen = False
    if goods:
        available = max(goods.quantity - goods.reserved_quantity, Decimal('0'))
        frozen = goods.is_frozen
    return {
        'kit_item_id': entry.item.id,
        'goods_id': entry.goods_id,
        'goods_name': goods.name if goods else '',
        'goods_code': goods.code if goods else '',
        'role': entry.item.role,
        'required': entry.item.required,
        'requested': str(entry.quantity),
        'available': str(available),
        'shortage': str(entry.quantity - available),
        'frozen': frozen,
        'blocking': blocking,
    }


def _compute_shortages(plan):
    """按最新库存重新计算整套缺货明细，说明整套能否交付"""
    shortages = []
    for entry in plan:
        goods = Goods.objects.filter(pk=entry.goods_id).first()
        available = Decimal('0')
        if goods and goods.is_active and not goods.is_frozen:
            available = max(goods.quantity - goods.reserved_quantity, Decimal('0'))
        if available < entry.quantity:
            shortages.append(_shortage_dict(entry, blocking=entry.item.required))
    return shortages


def _reserve_and_fill(application, plan):
    """在事务内逐项占用并生成申请明细；必需项缺货则整单回滚"""
    optional_shortages = []
    created_items = []
    for entry in plan:
        if reserve_goods(entry.goods_id, entry.quantity):
            created_items.append(KitApplicationItem(
                application=application,
                kit_item=entry.item,
                goods_id=entry.goods_id,
                role=entry.item.role,
                required=entry.item.required,
                is_substitute=entry.is_substitute,
                substitute_reason=entry.reason,
                requested_quantity=entry.quantity,
                reserved_quantity=entry.quantity,
                status='reserved',
            ))
        elif entry.item.required:
            raise RequiredItemShortage()
        else:
            optional_shortages.append(_shortage_dict(entry, blocking=False))
    KitApplicationItem.objects.bulk_create(created_items)
    application.status = 'pending'
    application.shortage_detail = optional_shortages
    application.save()
    return application


def _expand_application(application, plan, is_new):
    """把申请展开为具体占用：占用与申请在同一事务提交；
    必需项不足时不产生任何占用，申请落为缺货待补并说明缺货明细"""
    try:
        with transaction.atomic():
            application.save()
            return _reserve_and_fill(application, plan)
    except RequiredItemShortage:
        pass
    if is_new:
        # 回滚后丢弃已分配的主键，按缺货申请重新插入
        application.pk = None
    application.status = 'shortage'
    application.shortage_detail = _compute_shortages(plan)
    application.save()
    return application


def create_application(*, user, kit, receiver, receiver_dept, remark, substitute_choices):
    """创建领用申请：按当前已发布版本展开，必需项全部可用才进入审批"""
    if not kit.is_active:
        raise BusinessException('该领用包已停用')
    version = kit.current_version
    if version is None:
        raise BusinessException('该领用包暂无已发布版本')
    items = list(version.items.select_related('goods').prefetch_related('substitutes'))
    if not items:
        raise BusinessException('当前版本未定义物资项')
    plan = _build_plan(items, substitute_choices)
    application = KitApplication(
        kit_version=version,
        applicant=user,
        receiver=receiver,
        receiver_dept=receiver_dept,
        remark=remark,
        status='pending',
    )
    return _expand_application(application, plan, is_new=True)


def recheck_application(*, application, substitute_choices):
    """缺货申请重新校验：仍按申请时锁定的版本展开"""
    if application.status != 'shortage':
        raise BusinessException('仅缺货待补的申请可重新校验')
    version = application.kit_version
    items = list(version.items.select_related('goods').prefetch_related('substitutes'))
    plan = _build_plan(items, substitute_choices)
    return _expand_application(application, plan, is_new=False)


# ==================== 审批与发放 ====================

def approve_application(*, application, approver):
    if application.status != 'pending':
        raise BusinessException('仅待审批的申请可批准')
    application.status = 'approved'
    application.approved_by = approver
    application.approved_at = timezone.now()
    application.save()
    return application


def _release_all_items(application):
    """释放申请的全部占用（拒绝/取消时调用）"""
    for item in application.items.all():
        if item.reserved_quantity > 0:
            release_goods(item.goods_id, item.reserved_quantity)
        item.reserved_quantity = Decimal('0')
        item.status = 'released'
        item.save(update_fields=['reserved_quantity', 'status'])


def reject_application(*, application, approver, remark=''):
    if application.status != 'pending':
        raise BusinessException('仅待审批的申请可拒绝')
    with transaction.atomic():
        _release_all_items(application)
        application.status = 'rejected'
        application.approved_by = approver
        application.approved_at = timezone.now()
        if remark:
            application.remark = remark
        application.save()
    return application


def cancel_application(*, application):
    if application.status not in ('pending', 'shortage'):
        raise BusinessException('仅待审批或缺货待补的申请可取消')
    with transaction.atomic():
        _release_all_items(application)
        application.status = 'cancelled'
        application.save()
    return application


def issue_application(*, application):
    """发放：占用转为实际出库，任一货物被冻结则整单不发放"""
    if application.status != 'approved':
        raise BusinessException('仅已批准的申请可发放')
    items = list(application.items.select_related('goods'))
    frozen_names = [item.goods.name for item in items if item.goods.is_frozen]
    if frozen_names:
        raise BusinessException(f"货物「{'、'.join(frozen_names)}」已冻结，无法发放")
    with transaction.atomic():
        for item in items:
            deduct_reserved_goods(item.goods_id, item.reserved_quantity)
            item.issued_quantity = item.reserved_quantity
            item.reserved_quantity = Decimal('0')
            item.status = 'issued'
            item.save(update_fields=['issued_quantity', 'reserved_quantity', 'status'])
        application.status = 'issued'
        application.issued_at = timezone.now()
        application.save()
    return application


def return_application(*, application, returns):
    """部分/全部退回：逐项核销，数量与包状态保持一致"""
    if application.status not in ('issued', 'partially_returned'):
        raise BusinessException('仅已发放的申请可办理退回')
    if not returns:
        raise BusinessException('请选择要退回的物资')
    items_by_id = {item.id: item for item in application.items.select_related('goods')}
    with transaction.atomic():
        for entry in returns:
            item = items_by_id.get(entry['item'])
            if item is None:
                raise BusinessException('退回明细包含不属于该申请的物资项')
            quantity = entry['quantity']
            if quantity <= 0:
                raise BusinessException('退回数量必须大于0')
            # 守卫条件保证已退数量不会超过发放数量
            updated = KitApplicationItem.objects.filter(
                pk=item.pk,
                returned_quantity__lte=F('issued_quantity') - quantity,
            ).update(returned_quantity=F('returned_quantity') + quantity)
            if updated != 1:
                raise BusinessException(f'物资项"{item.goods.name}"退回数量超出可退余额')
            restock_goods(item.goods_id, quantity)
            KitApplicationItem.objects.filter(pk=item.pk).update(
                status=Case(
                    When(returned_quantity__gte=F('issued_quantity'), then=Value('returned')),
                    default=Value('partially_returned'),
                )
            )
        _sync_return_status(application)
    return application


def _sync_return_status(application):
    """按明细核销结果同步申请整体状态"""
    items = list(application.items.all())
    if items and all(item.returned_quantity >= item.issued_quantity for item in items):
        application.status = 'returned'
    else:
        application.status = 'partially_returned'
    application.save(update_fields=['status', 'updated_at'])
