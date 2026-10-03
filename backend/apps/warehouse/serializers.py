"""
仓库管理序列化器
"""
from rest_framework import serializers
from .models import (
    Unit, Category, Variety, Goods, StockIn, StockOut, Warning, Approval,
    KitTemplate, KitVersion, KitItemGroup, KitItem, StockFreeze,
    KitRequest, KitRequestLine,
)


class UnitSerializer(serializers.ModelSerializer):
    """单位序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = Unit
        fields = [
            'id', 'name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UnitCreateSerializer(serializers.Serializer):
    """单位创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=5, required=True, error_messages={
        'required': '请输入单位名称',
        'blank': '单位名称不能为空',
        'min_length': '单位名称至少1个字',
        'max_length': '单位名称最多5个字',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Unit.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('单位名称已存在')
        else:
            if Unit.objects.filter(name=value).exists():
                raise serializers.ValidationError('单位名称已存在')
        return value


class CategorySerializer(serializers.ModelSerializer):
    """品类序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    
    class Meta:
        model = Category
        fields = [
            'id', 'name', 'unit', 'unit_name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CategoryCreateSerializer(serializers.Serializer):
    """品类创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=10, required=True, error_messages={
        'required': '请输入品类名称',
        'blank': '品类名称不能为空',
        'min_length': '品类名称至少1个字',
        'max_length': '品类名称最多10个字',
    })
    unit = serializers.IntegerField(required=True, error_messages={
        'required': '请选择单位',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Category.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('品类名称已存在')
        else:
            if Category.objects.filter(name=value).exists():
                raise serializers.ValidationError('品类名称已存在')
        return value
    
    def validate_unit(self, value):
        if not Unit.objects.filter(pk=value).exists():
            raise serializers.ValidationError('单位不存在')
        return value


class VarietySerializer(serializers.ModelSerializer):
    """品种序列化器"""
    is_in_stock = serializers.BooleanField(read_only=True)
    unit_name = serializers.CharField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Variety
        fields = [
            'id', 'name', 'category', 'category_name', 'unit_name',
            'is_in_stock', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class VarietyCreateSerializer(serializers.Serializer):
    """品种创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=20, required=True, error_messages={
        'required': '请输入品种名称',
        'blank': '品种名称不能为空',
        'min_length': '品种名称至少1个字',
        'max_length': '品种名称最多20个字',
    })
    category = serializers.IntegerField(required=True, error_messages={
        'required': '请选择品类',
    })
    
    def validate_category(self, value):
        if not Category.objects.filter(pk=value).exists():
            raise serializers.ValidationError('品类不存在')
        return value
    
    def validate(self, data):
        instance = self.context.get('instance')
        name = data['name']
        category_id = data['category']
        
        if instance:
            if Variety.objects.filter(name=name, category_id=category_id).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        else:
            if Variety.objects.filter(name=name, category_id=category_id).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        return data


class GoodsSerializer(serializers.ModelSerializer):
    """货物序列化器"""
    variety_name = serializers.CharField(source='variety.name', read_only=True)
    category_name = serializers.CharField(source='variety.category.name', read_only=True)
    unit_name = serializers.CharField(source='variety.category.unit.name', read_only=True)
    available_quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    is_warning = serializers.BooleanField(read_only=True)

    class Meta:
        model = Goods
        fields = [
            'id', 'name', 'code', 'variety', 'variety_name',
            'category_name', 'unit_name', 'specification',
            'quantity', 'occupied_quantity', 'frozen_quantity', 'available_quantity',
            'warning_threshold', 'location',
            'remark', 'is_active', 'is_warning',
            'created_at', 'updated_at'
        ]


class StockInSerializer(serializers.ModelSerializer):
    """入库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    
    class Meta:
        model = StockIn
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'quantity', 'batch_no', 'supplier', 'stock_in_time', 'remark'
        ]


class StockOutSerializer(serializers.ModelSerializer):
    """出库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = StockOut
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'receiver', 'receiver_dept', 'quantity', 'status', 'status_display',
            'stock_out_time', 'remark', 'created_at'
        ]


class WarningSerializer(serializers.ModelSerializer):
    """预警记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    
    class Meta:
        model = Warning
        fields = [
            'id', 'goods', 'goods_name', 'type', 'type_display',
            'message', 'is_read', 'created_at'
        ]


class ApprovalSerializer(serializers.ModelSerializer):
    """审批记录序列化器"""
    approver_name = serializers.CharField(source='approver.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Approval
        fields = [
            'id', 'stock_out', 'approver', 'approver_name',
            'status', 'status_display', 'remark', 'created_at', 'updated_at'
        ]


# ==================== 领用包 ====================

class KitItemSerializer(serializers.ModelSerializer):
    """领用包明细序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    goods_code = serializers.CharField(source='goods.code', read_only=True)
    specification = serializers.CharField(source='goods.specification', read_only=True)
    available_quantity = serializers.DecimalField(
        source='goods.available_quantity', max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = KitItem
        fields = [
            'id', 'goods', 'goods_name', 'goods_code', 'specification',
            'quantity', 'required', 'order', 'available_quantity',
        ]


class KitItemGroupSerializer(serializers.ModelSerializer):
    """可替代项分组序列化器"""
    candidates = KitItemSerializer(many=True, read_only=True)

    class Meta:
        model = KitItemGroup
        fields = ['id', 'name', 'quantity', 'required', 'order', 'candidates']


class KitVersionSerializer(serializers.ModelSerializer):
    """领用包版本序列化器"""
    items = serializers.SerializerMethodField()
    groups = KitItemGroupSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    request_count = serializers.IntegerField(source='requests.count', read_only=True)

    class Meta:
        model = KitVersion
        fields = [
            'id', 'template', 'version_no', 'status', 'status_display',
            'change_remark', 'created_by', 'created_by_name',
            'request_count', 'items', 'groups', 'created_at', 'updated_at',
        ]

    def get_items(self, obj):
        return KitItemSerializer(obj.items.filter(group__isnull=True), many=True).data


class KitTemplateSerializer(serializers.ModelSerializer):
    """领用包定义序列化器"""
    active_version_no = serializers.SerializerMethodField()
    active_version_id = serializers.SerializerMethodField()
    version_count = serializers.IntegerField(source='versions.count', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = KitTemplate
        fields = [
            'id', 'name', 'description', 'is_active',
            'active_version_id', 'active_version_no', 'version_count',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]

    def get_active_version_no(self, obj):
        version = obj.active_version
        return version.version_no if version else None

    def get_active_version_id(self, obj):
        version = obj.active_version
        return version.id if version else None


class KitItemInputSerializer(serializers.Serializer):
    """版本明细输入（固定项或候选项通用）"""
    goods = serializers.IntegerField(required=True, error_messages={'required': '请选择货物'})
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=1, min_value=0
    )


class KitFixedItemInputSerializer(KitItemInputSerializer):
    required = serializers.BooleanField(required=False, default=True)


class KitGroupInputSerializer(serializers.Serializer):
    """可替代项分组输入"""
    name = serializers.CharField(max_length=100, required=True)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=1, min_value=0
    )
    required = serializers.BooleanField(required=False, default=True)
    candidates = KitItemInputSerializer(many=True, required=True)


class KitVersionWriteSerializer(serializers.Serializer):
    """版本创建/草稿编辑输入"""
    change_remark = serializers.CharField(max_length=500, required=False, default='', allow_blank=True)
    activate = serializers.BooleanField(required=False, default=False)
    items = KitFixedItemInputSerializer(many=True, required=False, default=list)
    groups = KitGroupInputSerializer(many=True, required=False, default=list)


class KitSelectionInputSerializer(serializers.Serializer):
    """发起申请时的物资选择"""
    goods = serializers.IntegerField(required=True)
    group = serializers.IntegerField(required=False, allow_null=True)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=0
    )
    reason = serializers.CharField(required=False, default='', allow_blank=True)


class KitRequestSubmitSerializer(serializers.Serializer):
    """领用申请提交输入"""
    version = serializers.IntegerField(required=True, error_messages={'required': '请选择领用包版本'})
    receiver = serializers.CharField(max_length=100, required=True)
    receiver_dept = serializers.CharField(max_length=100, required=False, default='', allow_blank=True)
    remark = serializers.CharField(required=False, default='', allow_blank=True)
    selections = KitSelectionInputSerializer(many=True, required=False, default=list)


class KitRequestPreviewSerializer(serializers.Serializer):
    """申请前整套可交付校验输入"""
    version = serializers.IntegerField(required=True)
    selections = KitSelectionInputSerializer(many=True, required=False, default=list)


class KitRequestLineSerializer(serializers.ModelSerializer):
    """领用申请明细序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    goods_code = serializers.CharField(source='goods.code', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = KitRequestLine
        fields = [
            'id', 'goods', 'goods_name', 'goods_code',
            'group', 'group_name', 'required', 'is_alternative',
            'selection_reason',
            'quantity', 'occupied_quantity', 'issued_quantity', 'returned_quantity',
            'status', 'status_display',
        ]


class KitRequestSerializer(serializers.ModelSerializer):
    """领用申请序列化器"""
    lines = KitRequestLineSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    template_name = serializers.CharField(source='template.name', read_only=True)
    version_no = serializers.IntegerField(source='version.version_no', read_only=True)
    applicant_name = serializers.CharField(source='applicant.username', read_only=True)
    approver_name = serializers.CharField(source='approved_by.username', read_only=True)

    class Meta:
        model = KitRequest
        fields = [
            'id', 'number', 'template', 'template_name',
            'version', 'version_no', 'applicant', 'applicant_name',
            'receiver', 'receiver_dept', 'status', 'status_display',
            'remark', 'approved_by', 'approver_name', 'approved_at',
            'lines', 'created_at', 'updated_at',
        ]


class StockFreezeSerializer(serializers.ModelSerializer):
    """物资冻结记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)

    class Meta:
        model = StockFreeze
        fields = [
            'id', 'goods', 'goods_name', 'type', 'type_display',
            'quantity', 'reason', 'operator', 'operator_name', 'created_at',
        ]


class FreezeInputSerializer(serializers.Serializer):
    """冻结/解冻输入"""
    goods = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=True, min_value=0
    )
    reason = serializers.CharField(required=True, allow_blank=False)


class ReturnLineInputSerializer(serializers.Serializer):
    """退回明细输入"""
    line = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=True, min_value=0
    )
    reason = serializers.CharField(required=False, default='', allow_blank=True)


class ReturnInputSerializer(serializers.Serializer):
    """退回申请输入"""
    items = ReturnLineInputSerializer(many=True, required=True)
