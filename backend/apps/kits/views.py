"""
领用包视图
"""
import logging

from django.db import transaction
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from apps.core.exceptions import BusinessException
from apps.core.response import success_response, error_response
from .models import Kit, KitApplication, KitVersion
from .serializers import (
    KitSerializer, KitCreateSerializer, KitUpdateSerializer,
    KitVersionSerializer, KitVersionBriefSerializer, KitVersionCreateSerializer,
    KitItemInputSerializer, SubstituteChoiceSerializer,
    KitApplicationSerializer, KitApplicationCreateSerializer,
    KitApplicationReturnSerializer,
)
from . import services

logger = logging.getLogger('apps')


def _first_error(errors):
    """提取序列化器的首个错误信息（兼容嵌套的列表/字典结构）"""
    if isinstance(errors, dict):
        for value in errors.values():
            message = _first_error(value)
            if message:
                return message
    elif isinstance(errors, list):
        for item in errors:
            message = _first_error(item)
            if message:
                return message
    elif errors:
        return str(errors)
    return ''


def _paginate(request, queryset):
    """按 page/page_size 参数分页"""
    page = int(request.query_params.get('page', 1))
    page_size = int(request.query_params.get('page_size', 10))
    start = (page - 1) * page_size
    end = start + page_size
    return {
        'list': queryset[start:end],
        'total': queryset.count(),
        'page': page,
        'page_size': page_size,
    }


# ==================== 领用包定义 ====================

class KitListView(APIView):
    """领用包列表视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = Kit.objects.all().order_by('-created_at')
        keyword = request.query_params.get('keyword', '').strip()
        if keyword:
            queryset = queryset.filter(name__icontains=keyword)
        is_active = request.query_params.get('is_active')
        if is_active in ('true', 'false'):
            queryset = queryset.filter(is_active=(is_active == 'true'))

        result = _paginate(request, queryset)
        result['list'] = KitSerializer(result['list'], many=True).data
        return success_response(data=result)

    def post(self, request):
        """创建领用包（可同时携带首个版本的物资项）"""
        serializer = KitCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer.errors) or '请求参数错误')

        data = serializer.validated_data
        with transaction.atomic():
            kit = Kit.objects.create(
                name=data['name'],
                code=data['code'],
                description=data['description'],
                created_by=request.user,
            )
            if data['items']:
                version = KitVersion.objects.create(
                    kit=kit, version_no=1, created_by=request.user
                )
                services.replace_version_items(version, data['items'])

        logger.info(f"User {request.user.username} created kit {kit.name}")

        return success_response(data=KitSerializer(kit).data, message='创建成功')


class KitDetailView(APIView):
    """领用包详情视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            kit = Kit.objects.get(pk=pk)
        except Kit.DoesNotExist:
            return error_response(message='领用包不存在', code=404)

        data = KitSerializer(kit).data
        data['versions'] = KitVersionBriefSerializer(kit.versions.all(), many=True).data
        return success_response(data=data)

    def put(self, request, pk):
        """更新领用包基本信息"""
        try:
            kit = Kit.objects.get(pk=pk)
        except Kit.DoesNotExist:
            return error_response(message='领用包不存在', code=404)

        serializer = KitUpdateSerializer(data=request.data, context={'instance': kit})
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer.errors) or '请求参数错误')

        kit.name = serializer.validated_data['name']
        kit.description = serializer.validated_data['description']
        kit.is_active = serializer.validated_data['is_active']
        kit.save()

        logger.info(f"User {request.user.username} updated kit {kit.name}")

        return success_response(data=KitSerializer(kit).data, message='更新成功')

    def delete(self, request, pk):
        """删除领用包：已产生领用申请的包不允许删除"""
        try:
            kit = Kit.objects.get(pk=pk)
        except Kit.DoesNotExist:
            return error_response(message='领用包不存在', code=404)

        if KitApplication.objects.filter(kit_version__kit=kit).exists():
            return error_response(message='该领用包已产生领用申请，无法删除')

        name = kit.name
        kit.delete()

        logger.info(f"User {request.user.username} deleted kit {name}")

        return success_response(message='删除成功')


class KitVersionListView(APIView):
    """领用包版本列表视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            kit = Kit.objects.get(pk=pk)
        except Kit.DoesNotExist:
            return error_response(message='领用包不存在', code=404)

        versions = kit.versions.prefetch_related('items__goods', 'items__substitutes')
        return success_response(data=KitVersionSerializer(versions, many=True).data)

    def post(self, request, pk):
        """新建版本（包定义升级）：携带物资项则按之建立，否则复制当前已发布版本"""
        try:
            kit = Kit.objects.get(pk=pk)
        except Kit.DoesNotExist:
            return error_response(message='领用包不存在', code=404)

        serializer = KitVersionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer.errors) or '请求参数错误')

        data = serializer.validated_data
        last_no = kit.versions.order_by('-version_no').values_list('version_no', flat=True).first() or 0

        with transaction.atomic():
            version = KitVersion.objects.create(
                kit=kit,
                version_no=last_no + 1,
                note=data['note'],
                created_by=request.user,
            )
            items_data = data['items']
            if items_data is None:
                current = kit.current_version
                if current:
                    items_data = [
                        {
                            'goods': item.goods_id,
                            'role': item.role,
                            'quantity': item.quantity,
                            'required': item.required,
                            'substitutes': list(item.substitutes.values_list('id', flat=True)),
                            'sort_order': item.sort_order,
                        }
                        for item in current.items.prefetch_related('substitutes')
                    ]
                else:
                    items_data = []
            if items_data:
                services.replace_version_items(version, items_data)

        logger.info(f"User {request.user.username} created version v{version.version_no} for kit {kit.name}")

        return success_response(data=KitVersionSerializer(version).data, message='创建成功')


class KitVersionDetailView(APIView):
    """领用包版本详情视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            version = KitVersion.objects.get(pk=pk)
        except KitVersion.DoesNotExist:
            return error_response(message='版本不存在', code=404)

        return success_response(data=KitVersionSerializer(version).data)

    def put(self, request, pk):
        """整体替换草稿版本的物资项"""
        try:
            version = KitVersion.objects.get(pk=pk)
        except KitVersion.DoesNotExist:
            return error_response(message='版本不存在', code=404)

        if version.status != 'draft':
            return error_response(message='仅草稿版本可修改物资项')

        items = request.data.get('items')
        if not isinstance(items, list) or not items:
            return error_response(message='请提供物资项列表')

        serializer = KitItemInputSerializer(data=items, many=True)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer.errors) or '请求参数错误')

        services.replace_version_items(version, serializer.validated_data)

        logger.info(f"User {request.user.username} updated items of kit version {version}")

        return success_response(data=KitVersionSerializer(version).data, message='更新成功')

    def delete(self, request, pk):
        """删除草稿版本"""
        try:
            version = KitVersion.objects.get(pk=pk)
        except KitVersion.DoesNotExist:
            return error_response(message='版本不存在', code=404)

        if version.status != 'draft':
            return error_response(message='仅草稿版本可删除')

        version.delete()

        logger.info(f"User {request.user.username} deleted kit version {version}")

        return success_response(message='删除成功')


class KitVersionPublishView(APIView):
    """版本发布视图"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            version = KitVersion.objects.get(pk=pk)
        except KitVersion.DoesNotExist:
            return error_response(message='版本不存在', code=404)

        try:
            services.publish_version(version)
        except BusinessException as exc:
            return error_response(message=exc.message, code=exc.code)

        logger.info(f"User {request.user.username} published kit version {version}")

        return success_response(data=KitVersionSerializer(version).data, message='发布成功')


# ==================== 领用申请 ====================

class KitApplicationListView(APIView):
    """领用申请列表视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = KitApplication.objects.select_related(
            'kit_version__kit', 'applicant', 'approved_by'
        ).prefetch_related('items__goods').order_by('-created_at')

        status = request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)
        kit_id = request.query_params.get('kit_id')
        if kit_id:
            queryset = queryset.filter(kit_version__kit_id=kit_id)

        result = _paginate(request, queryset)
        result['list'] = KitApplicationSerializer(result['list'], many=True).data
        return success_response(data=result)

    def post(self, request):
        """创建领用申请：按当前已发布版本展开为具体物资占用"""
        serializer = KitApplicationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer.errors) or '请求参数错误')

        data = serializer.validated_data
        try:
            application = services.create_application(
                user=request.user,
                kit=Kit.objects.get(pk=data['kit']),
                receiver=data['receiver'],
                receiver_dept=data['receiver_dept'],
                remark=data['remark'],
                substitute_choices=data['substitute_choices'],
            )
        except BusinessException as exc:
            return error_response(message=exc.message, code=exc.code)

        logger.info(
            f"User {request.user.username} created kit application {application.id} "
            f"for kit {application.kit_version.kit.name} -> {application.status}"
        )

        message = '申请已提交审批' if application.status == 'pending' else '必需物资缺货，申请待补货'
        return success_response(data=KitApplicationSerializer(application).data, message=message)


class KitApplicationDetailView(APIView):
    """领用申请详情视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            application = KitApplication.objects.get(pk=pk)
        except KitApplication.DoesNotExist:
            return error_response(message='领用申请不存在', code=404)

        return success_response(data=KitApplicationSerializer(application).data)


class _KitApplicationActionView(APIView):
    """申请操作基类：取申请、调用服务、统一响应"""
    permission_classes = [IsAuthenticated]

    def get_application(self, pk):
        try:
            return KitApplication.objects.get(pk=pk)
        except KitApplication.DoesNotExist:
            return None

    def run(self, request, application):
        raise NotImplementedError

    def post(self, request, pk):
        application = self.get_application(pk)
        if application is None:
            return error_response(message='领用申请不存在', code=404)
        try:
            application = self.run(request, application)
        except BusinessException as exc:
            return error_response(message=exc.message, code=exc.code)

        logger.info(
            f"User {request.user.username} {self.action_name} kit application {application.id}"
        )

        return success_response(
            data=KitApplicationSerializer(application).data, message=self.success_message
        )


class KitApplicationApproveView(_KitApplicationActionView):
    """批准申请"""
    action_name = 'approved'
    success_message = '批准成功'

    def run(self, request, application):
        return services.approve_application(application=application, approver=request.user)


class KitApplicationRejectView(_KitApplicationActionView):
    """拒绝申请并释放占用"""
    action_name = 'rejected'
    success_message = '已拒绝'

    def run(self, request, application):
        return services.reject_application(
            application=application,
            approver=request.user,
            remark=str(request.data.get('remark', '')).strip(),
        )


class KitApplicationCancelView(_KitApplicationActionView):
    """取消申请并释放占用"""
    action_name = 'cancelled'
    success_message = '已取消'

    def run(self, request, application):
        return services.cancel_application(application=application)


class KitApplicationIssueView(_KitApplicationActionView):
    """发放：占用转为实际出库"""
    action_name = 'issued'
    success_message = '发放成功'

    def run(self, request, application):
        return services.issue_application(application=application)


class KitApplicationRecheckView(_KitApplicationActionView):
    """缺货申请重新校验"""
    action_name = 'rechecked'
    success_message = '重新校验完成'

    def run(self, request, application):
        choices = request.data.get('substitute_choices', [])
        serializer = SubstituteChoiceSerializer(data=choices, many=True)
        if not serializer.is_valid():
            raise BusinessException(_first_error(serializer.errors) or '请求参数错误')
        return services.recheck_application(
            application=application,
            substitute_choices=serializer.validated_data,
        )


class KitApplicationReturnView(APIView):
    """退回（支持部分退回）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            application = KitApplication.objects.get(pk=pk)
        except KitApplication.DoesNotExist:
            return error_response(message='领用申请不存在', code=404)

        returns = request.data.get('items')
        if not isinstance(returns, list):
            return error_response(message='请提供退回明细')

        serializer = KitApplicationReturnSerializer(data=returns, many=True)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer.errors) or '请求参数错误')

        try:
            application = services.return_application(
                application=application,
                returns=serializer.validated_data,
            )
        except BusinessException as exc:
            return error_response(message=exc.message, code=exc.code)

        logger.info(f"User {request.user.username} returned items of kit application {application.id}")

        return success_response(
            data=KitApplicationSerializer(application).data, message='退回成功'
        )
