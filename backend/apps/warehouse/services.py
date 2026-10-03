"""
领用包业务服务。

所有库存数量变动都以「条件更新」落账：UPDATE ... WHERE 数量足够。
两个申请并发争用同一配件时，数据库层面只会有一个事务满足条件，
失败方整单回滚——不依赖 SELECT FOR UPDATE（SQLite 不支持），
在 PostgreSQL/MySQL 上同样成立。
"""
from decimal import Decimal
import functools
import random
import time

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from apps.core.exceptions import BusinessException, PermissionException
from .models import (
    Goods, KitItem, KitRequest, KitRequestLine,
    KitTemplate, KitVersion, StockFreeze, StockIn, StockOut,
)

ZERO = Decimal('0')


def _require_admin(actor, action):
    if not getattr(actor, 'is_admin', False):
        raise PermissionException(f'仅管理员可{action}')


def retry_on_locked(func):
    """SQLite 同进程读写冲突可能立刻报 database/table is locked，短暂退避后重试。"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(8):
            try:
                return func(*args, **kwargs)
            except OperationalError as exc:
                if 'locked' not in str(exc).lower():
                    raise
                last_exc = exc
                time.sleep(0.02 * (attempt + 1) + random.random() * 0.02)
        raise last_exc
    return wrapper


def _clean_qty(value, field='数量'):
    try:
        qty = Decimal(str(value))
    except Exception:
        raise BusinessException(f'{field}必须为数字')
    if qty <= 0:
        raise BusinessException(f'{field}必须大于0')
    return qty.quantize(Decimal('0.01'))


def _load_goods(goods_ids):
    """按主键排序读取货物，返回 {id: goods}。"""
    ids = sorted(set(goods_ids))
    rows = {row.pk: row for row in Goods.objects.filter(pk__in=ids)}
    if len(rows) != len(ids):
        raise BusinessException('货物不存在')
    return rows


def _get_goods(goods_id):
    if goods_id is None:
        raise BusinessException('请选择货物')
    try:
        return Goods.objects.get(pk=goods_id)
    except Goods.DoesNotExist:
        raise BusinessException('货物不存在')


def _add_occupied(goods_id, qty):
    """原子占用：仅当 库存 >= 占用+冻结+本次需求 时成功。"""
    updated = Goods.objects.filter(
        pk=goods_id, is_active=True,
        quantity__gte=F('occupied_quantity') + F('frozen_quantity') + qty,
    ).update(occupied_quantity=F('occupied_quantity') + qty)
    if not updated:
        name = Goods.objects.filter(pk=goods_id).values_list('name', flat=True).first()
        raise BusinessException(f'货物“{name or goods_id}”库存不足或已停用')


def _sub_occupied(goods_id, qty):
    """原子释放占用，占用数不足说明数据已被并发改动，整单回滚。"""
    updated = Goods.objects.filter(
        pk=goods_id, occupied_quantity__gte=qty,
    ).update(occupied_quantity=F('occupied_quantity') - qty)
    if not updated:
        raise BusinessException('库存占用数据已变动，请刷新后重试')


# ==================== 领用包定义与版本 ====================

@retry_on_locked
@transaction.atomic
def create_template(name, description, actor):
    _require_admin(actor, '维护领用包定义')
    name = (name or '').strip()
    if not name:
        raise BusinessException('请输入领用包名称')
    if KitTemplate.objects.filter(name=name).exists():
        raise BusinessException('领用包名称已存在')
    return KitTemplate.objects.create(name=name, description=description or '', created_by=actor)


@retry_on_locked
@transaction.atomic
def update_template(template, name, description, actor):
    _require_admin(actor, '维护领用包定义')
    name = (name or '').strip()
    if not name:
        raise BusinessException('请输入领用包名称')
    if KitTemplate.objects.filter(name=name).exclude(pk=template.pk).exists():
        raise BusinessException('领用包名称已存在')
    template.name = name
    template.description = description or ''
    template.save()
    return template


@retry_on_locked
@transaction.atomic
def delete_template(template, actor):
    _require_admin(actor, '维护领用包定义')
    if template.versions.filter(requests__isnull=False).exists():
        raise BusinessException('该领用包已有领用记录，不可删除')
    name = template.name
    template.delete()
    return name


def _validate_version_payload(items, groups):
    """校验版本内容，返回 (固定项列表, 分组列表)。"""
    if not items and not groups:
        raise BusinessException('领用包至少包含一项物资')

    normalized_groups = []
    group_names = set()
    for idx, group in enumerate(groups or []):
        gname = (group.get('name') or '').strip()
        if not gname:
            raise BusinessException(f'第{idx + 1}个分组缺少名称')
        if gname in group_names:
            raise BusinessException(f'分组名称重复：{gname}')
        group_names.add(gname)
        candidates = group.get('candidates') or []
        if not candidates:
            raise BusinessException(f'分组“{gname}”至少需要一个可替代项')
        norm_candidates = []
        candidate_goods = set()
        for c in candidates:
            goods = _get_goods(c.get('goods'))
            if goods.pk in candidate_goods:
                raise BusinessException(f'分组“{gname}”内物资重复：{goods.name}')
            if not goods.is_active:
                raise BusinessException(f'货物“{goods.name}”已停用，不能加入领用包')
            candidate_goods.add(goods.pk)
            norm_candidates.append((goods, _clean_qty(c.get('quantity', 1), '替代项数量')))
        normalized_groups.append({
            'name': gname,
            'quantity': _clean_qty(group.get('quantity', 1), '分组需求数量'),
            'required': bool(group.get('required', True)),
            'order': idx,
            'candidates': norm_candidates,
        })

    normalized_items = []
    fixed_goods = set()
    for idx, item in enumerate(items or []):
        goods = _get_goods(item.get('goods'))
        if goods.pk in fixed_goods:
            raise BusinessException(f'固定项物资重复：{goods.name}')
        if not goods.is_active:
            raise BusinessException(f'货物“{goods.name}”已停用，不能加入领用包')
        fixed_goods.add(goods.pk)
        normalized_items.append({
            'goods': goods,
            'quantity': _clean_qty(item.get('quantity', 1), '物资数量'),
            'required': bool(item.get('required', True)),
            'order': idx,
        })

    # 同一物资不允许同时作为固定项与替代项，避免展开时重复占用
    group_goods = {c[0].pk for g in normalized_groups for c in g['candidates']}
    overlap = fixed_goods & group_goods
    if overlap:
        names = Goods.objects.filter(pk__in=overlap).values_list('name', flat=True)
        raise BusinessException(f'以下物资不能同时为固定项与可替代项：{"、".join(names)}')

    return normalized_items, normalized_groups


def _persist_version(version, items, groups):
    version.groups.all().delete()
    version.items.all().delete()
    for item in items:
        KitItem.objects.create(
            version=version, goods=item['goods'],
            quantity=item['quantity'], required=item['required'], order=item['order'],
        )
    for group in groups:
        obj = version.groups.create(
            name=group['name'], quantity=group['quantity'],
            required=group['required'], order=group['order'],
        )
        for goods, qty in group['candidates']:
            obj.candidates.create(version=version, goods=goods, quantity=qty, order=obj.order)


@retry_on_locked
@transaction.atomic
def create_version(template, items, groups, change_remark, actor, activate=False):
    _require_admin(actor, '维护领用包定义')
    items, groups = _validate_version_payload(items, groups)
    version_no = (template.versions.order_by('-version_no').values_list('version_no', flat=True).first() or 0) + 1
    try:
        version = KitVersion.objects.create(
            template=template, version_no=version_no,
            status='draft', change_remark=change_remark or '', created_by=actor,
        )
    except IntegrityError:
        # 并发创建同一包版本：版本号唯一约束兜底
        raise BusinessException('版本号冲突，请重试')
    _persist_version(version, items, groups)
    if activate:
        _activate_version(version)
    return version


@retry_on_locked
@transaction.atomic
def update_draft_version(version, change_remark, items, groups, actor):
    _require_admin(actor, '维护领用包定义')
    if version.is_immutable:
        raise BusinessException('已生效版本不可修改，请升级新版本')
    items, groups = _validate_version_payload(items, groups)
    version.change_remark = change_remark or ''
    version.save(update_fields=['change_remark', 'updated_at'])
    _persist_version(version, items, groups)
    return version


@retry_on_locked
@transaction.atomic
def delete_version(version, actor):
    _require_admin(actor, '维护领用包定义')
    if version.is_immutable:
        raise BusinessException('已生效版本不可删除')
    if version.requests.exists():
        raise BusinessException('该版本已被领用申请引用，不可删除')
    version.delete()


@retry_on_locked
@transaction.atomic
def activate_version(version, actor):
    _require_admin(actor, '维护领用包定义')
    if version.status == 'archived':
        raise BusinessException('已归档版本不可重新生效，请基于当前内容升级新版本')
    return _activate_version(version)


def _activate_version(version):
    # 同一包同时只允许一个生效版本；旧生效版本归档
    KitVersion.objects.filter(template_id=version.template_id, status='active').exclude(
        pk=version.pk
    ).update(status='archived')
    changed = KitVersion.objects.filter(pk=version.pk).exclude(status='archived').update(status='active')
    if not changed:
        raise BusinessException('版本状态异常，无法生效')
    version.refresh_from_db()
    return version


# ==================== 领用申请：展开与占用 ====================

def _expand_requirements(version, selections):
    """
    将版本定义结合申请人的替代项选择展开为具体物资需求。

    selections: [{"goods": id, "group": 分组id|None,
                  "quantity": 可选覆盖数量, "reason": 选择依据}]
    """
    selections = selections or []
    fixed_items = {
        item.goods_id: item
        for item in version.items.filter(group__isnull=True)
    }
    groups = {
        group.pk: group
        for group in version.groups.prefetch_related('candidates').all()
    }

    chosen = {}      # goods_id -> 需求行
    group_pick = {}  # group_id -> goods_id

    for raw in selections:
        goods = _get_goods(raw.get('goods'))
        group_id = raw.get('group')
        reason = (raw.get('reason') or '').strip()

        if group_id is not None:
            group = groups.get(group_id)
            if group is None:
                raise BusinessException('替代分组不存在或不属于该版本')
            candidate = next((c for c in group.candidates.all() if c.goods_id == goods.pk), None)
            if candidate is None:
                raise BusinessException(f'货物“{goods.name}”不在分组“{group.name}”的可替代项中')
            if not reason:
                raise BusinessException(f'分组“{group.name}”选择可替代项时必须记录选择依据')
            if group_id in group_pick:
                raise BusinessException(f'分组“{group.name}”只能选择一项物资')
            qty = _clean_qty(raw['quantity']) if raw.get('quantity') is not None else group.quantity
            group_pick[group_id] = goods.pk
            chosen[goods.pk] = {
                'goods_id': goods.pk, 'quantity': qty,
                'group_id': group.pk, 'group_name': group.name,
                'required': True, 'is_alternative': True, 'reason': reason,
            }
        else:
            item = fixed_items.get(goods.pk)
            if item is None:
                raise BusinessException(f'货物“{goods.name}”不在该领用包版本中')
            if item.required:
                raise BusinessException(f'必需项“{goods.name}”无需重复选择')
            qty = _clean_qty(raw['quantity']) if raw.get('quantity') is not None else item.quantity
            chosen[goods.pk] = {
                'goods_id': goods.pk, 'quantity': qty,
                'group_id': None, 'group_name': '',
                'required': False, 'is_alternative': False, 'reason': '',
            }

    # 固定必需项全部纳入
    for item in fixed_items.values():
        if item.required and item.goods_id not in chosen:
            chosen[item.goods_id] = {
                'goods_id': item.goods_id, 'quantity': item.quantity,
                'group_id': None, 'group_name': '',
                'required': True, 'is_alternative': False, 'reason': '',
            }

    # 必需分组必须做出选择
    for gid, group in groups.items():
        if group.required and gid not in group_pick:
            raise BusinessException(f'必需分组“{group.name}”尚未选择替代物资')

    return list(chosen.values())


def _check_availability(requirements, loaded):
    """返回每个需求的可用情况；shortage 非空表示整套不可交付。"""
    details, shortage = [], []
    for req in requirements:
        goods = loaded[req['goods_id']]
        available = goods.quantity - goods.occupied_quantity - goods.frozen_quantity
        enough = goods.is_active and available >= req['quantity']
        details.append({
            'goods': goods.pk,
            'goods_name': goods.name,
            'required_quantity': str(req['quantity']),
            'available_quantity': str(max(available, ZERO)),
            'group_name': req['group_name'],
            'is_alternative': req['is_alternative'],
            'selection_reason': req['reason'],
            'available': enough,
        })
        if not enough:
            shortage.append(goods.name)
    return details, shortage


def _get_active_version(version_id):
    try:
        version = KitVersion.objects.select_related('template').get(pk=version_id)
    except KitVersion.DoesNotExist:
        raise BusinessException('领用包版本不存在', code=404)
    if version.status != 'active':
        raise BusinessException('仅生效中的领用包版本可发起申请')
    return version


def preview_request(version_id, selections):
    """校验整套物资当前是否可交付，不落任何数据（只读，autocommit 下执行）。"""
    version = _get_active_version(version_id)
    requirements = _expand_requirements(version, selections)
    loaded = _load_goods([r['goods_id'] for r in requirements])
    details, shortage = _check_availability(requirements, loaded)
    return {
        'deliverable': not shortage,
        'template': version.template_id,
        'version': version.pk,
        'version_no': version.version_no,
        'items': details,
        'shortage': shortage,
        'message': '整套物资均可交付' if not shortage else f'库存不足：{"、".join(shortage)}',
    }


@retry_on_locked
@transaction.atomic
def submit_request(actor, version_id, receiver, receiver_dept, remark, selections):
    return _submit_request_core(actor, version_id, receiver, receiver_dept, remark, selections)


def _submit_request_core(actor, version_id, receiver, receiver_dept, remark, selections):
    receiver = (receiver or '').strip()
    if not receiver:
        raise BusinessException('请填写领用人')
    version = _get_active_version(version_id)
    requirements = _expand_requirements(version, selections)

    # 先给出可读的整单缺口提示
    loaded = _load_goods([r['goods_id'] for r in requirements])
    _, shortage = _check_availability(requirements, loaded)
    if shortage:
        raise BusinessException(f'库存不足，整套无法交付：{"、".join(shortage)}')

    request = KitRequest.objects.create(
        template=version.template, version=version, applicant=actor,
        receiver=receiver, receiver_dept=receiver_dept or '', remark=remark or '',
        status='pending',
    )
    request.number = f'KR{timezone.localdate():%Y%m%d}{request.pk:06d}'
    request.save(update_fields=['number'])

    KitRequestLine.objects.bulk_create([
        KitRequestLine(
            request=request, goods_id=req['goods_id'],
            group_id=req['group_id'], group_name=req['group_name'],
            required=req['required'], is_alternative=req['is_alternative'],
            selection_reason=req['reason'],
            quantity=req['quantity'], occupied_quantity=req['quantity'],
            status='occupied',
        )
        for req in requirements
    ])

    # 原子条件占用：并发争用时未抢到的一方在此抛错并回滚整单
    for req in requirements:
        _add_occupied(req['goods_id'], req['quantity'])

    return request


def _release_occupancy(request):
    """释放申请尚在占用的全部数量（驳回/撤回时调用）。"""
    lines = list(request.lines.exclude(occupied_quantity=0))
    for line in lines:
        _sub_occupied(line.goods_id, line.occupied_quantity)
    request.lines.all().update(occupied_quantity=ZERO, status='released')


@retry_on_locked
@transaction.atomic
def approve_request(pk, actor, approved, opinion=''):
    _require_admin(actor, '审批领用申请')
    try:
        request = KitRequest.objects.get(pk=pk)
    except KitRequest.DoesNotExist:
        raise BusinessException('领用申请不存在', code=404)
    if request.status != 'pending':
        raise BusinessException('仅待审批申请可审批')

    if not approved:
        _release_occupancy(request)

    changed = KitRequest.objects.filter(pk=pk, status='pending').update(
        status='approved' if approved else 'rejected',
        approved_by=actor, approved_at=timezone.now(),
    )
    if not changed:
        raise BusinessException('申请状态已被并发变更，请刷新后重试')
    if opinion:
        KitRequest.objects.filter(pk=pk).update(
            remark=f'{request.remark}\n审批意见：{opinion}'.strip()
        )
    request.refresh_from_db()
    return request


@retry_on_locked
@transaction.atomic
def cancel_request(pk, actor):
    try:
        request = KitRequest.objects.get(pk=pk)
    except KitRequest.DoesNotExist:
        raise BusinessException('领用申请不存在', code=404)
    if request.applicant_id != actor.pk and not actor.is_admin:
        raise BusinessException('仅申请人或管理员可撤回申请', code=403)
    if request.status != 'pending':
        raise BusinessException('仅待审批申请可撤回')
    _release_occupancy(request)
    changed = KitRequest.objects.filter(pk=pk, status='pending').update(status='cancelled')
    if not changed:
        raise BusinessException('申请状态已被并发变更，请刷新后重试')
    request.refresh_from_db()
    return request


@retry_on_locked
@transaction.atomic
def issue_request(pk, actor):
    _require_admin(actor, '发放物资')
    try:
        request = KitRequest.objects.get(pk=pk)
    except KitRequest.DoesNotExist:
        raise BusinessException('领用申请不存在', code=404)
    if request.status != 'approved':
        raise BusinessException('仅已批准申请可发放')

    lines = list(request.lines.filter(occupied_quantity__gt=0))
    stock_outs = []
    for line in lines:
        qty = line.occupied_quantity
        # 发放即出库：库存与占用同步核减，均以条件更新防止并发超发
        updated = Goods.objects.filter(
            pk=line.goods_id, quantity__gte=qty, occupied_quantity__gte=qty,
        ).update(quantity=F('quantity') - qty, occupied_quantity=F('occupied_quantity') - qty)
        if not updated:
            goods = Goods.objects.filter(pk=line.goods_id).first()
            raise BusinessException(f'货物“{goods.name if goods else line.goods_id}”库存数据异常，无法发放')
        line.issued_quantity = (line.issued_quantity or ZERO) + qty
        line.occupied_quantity = ZERO
        line.status = 'issued'
        line.save(update_fields=['issued_quantity', 'occupied_quantity', 'status'])
        stock_outs.append(StockOut(
            goods_id=line.goods_id, operator=actor, receiver=request.receiver,
            receiver_dept=request.receiver_dept, quantity=qty,
            status='completed', stock_out_time=timezone.now(),
            remark=f'领用包发放 {request.number}',
        ))
    StockOut.objects.bulk_create(stock_outs)
    changed = KitRequest.objects.filter(pk=pk, status='approved').update(status='issued')
    if not changed:
        raise BusinessException('申请状态已被并发变更，请刷新后重试')
    request.refresh_from_db()
    return request


@retry_on_locked
@transaction.atomic
def return_request(pk, actor, items):
    _require_admin(actor, '办理退回')
    try:
        request = KitRequest.objects.get(pk=pk)
    except KitRequest.DoesNotExist:
        raise BusinessException('领用申请不存在', code=404)
    if request.status not in ('issued', 'partial_returned'):
        raise BusinessException('仅已发放申请可办理退回')

    items = items or []
    if not items:
        raise BusinessException('请填写退回明细')

    lines = {line.pk: line for line in request.lines.all()}
    plan = []
    for raw in items:
        line = lines.get(raw.get('line'))
        if line is None:
            raise BusinessException('退回明细不属于该申请')
        qty = _clean_qty(raw.get('quantity'), '退回数量')
        reason = (raw.get('reason') or '').strip()
        if line.issued_quantity - line.returned_quantity < qty:
            raise BusinessException(
                f'货物“{line.goods.name}”可退数量不足，退回数量超出'
            )
        plan.append((line, qty, reason))

    stock_ins = []
    for line, qty, reason in plan:
        # 行级条件更新：同一申请并发退回时只有一方满足条件
        changed = KitRequestLine.objects.filter(
            pk=line.pk,
            returned_quantity__lte=F('issued_quantity') - qty,
        ).update(returned_quantity=F('returned_quantity') + qty)
        if not changed:
            raise BusinessException(f'货物“{line.goods.name}”退回数量已被并发变更，请刷新后重试')
        Goods.objects.filter(pk=line.goods_id).update(quantity=F('quantity') + qty)
        stock_ins.append(StockIn(
            goods_id=line.goods_id, operator=actor, quantity=qty,
            batch_no=request.number,
            supplier=f'领用退回：{reason}' if reason else '领用退回',
        ))
    StockIn.objects.bulk_create(stock_ins)

    # 依据最新行状态重算每行与整单状态
    refreshed = request.lines.all()
    for line in refreshed:
        if line.returned_quantity == 0:
            line.status = 'issued'
        elif line.returned_quantity == line.issued_quantity:
            line.status = 'returned'
        else:
            line.status = 'partial_returned'
        line.save(update_fields=['status'])
    all_returned = all(line.returned_quantity == line.issued_quantity for line in refreshed)
    KitRequest.objects.filter(pk=pk).update(
        status='returned' if all_returned else 'partial_returned'
    )
    request.refresh_from_db()
    return request


# ==================== 物资冻结 ====================

@retry_on_locked
@transaction.atomic
def freeze_goods(goods_id, actor, quantity, reason):
    _require_admin(actor, '冻结物资')
    qty = _clean_qty(quantity, '冻结数量')
    reason = (reason or '').strip()
    if not reason:
        raise BusinessException('请填写冻结原因')
    goods = _get_goods(goods_id)
    updated = Goods.objects.filter(
        pk=goods.pk,
        quantity__gte=F('frozen_quantity') + F('occupied_quantity') + qty,
    ).update(frozen_quantity=F('frozen_quantity') + qty)
    if not updated:
        raise BusinessException('可冻结数量不足（已占用或已冻结部分不能重复冻结）')
    return StockFreeze.objects.create(
        goods=goods, type='freeze', quantity=qty, reason=reason, operator=actor
    )


@retry_on_locked
@transaction.atomic
def unfreeze_goods(goods_id, actor, quantity, reason):
    _require_admin(actor, '解冻物资')
    qty = _clean_qty(quantity, '解冻数量')
    reason = (reason or '').strip()
    if not reason:
        raise BusinessException('请填写解冻原因')
    goods = _get_goods(goods_id)
    updated = Goods.objects.filter(
        pk=goods.pk, frozen_quantity__gte=qty,
    ).update(frozen_quantity=F('frozen_quantity') - qty)
    if not updated:
        raise BusinessException('冻结数量不足，无法解冻')
    return StockFreeze.objects.create(
        goods=goods, type='unfreeze', quantity=qty, reason=reason, operator=actor
    )
