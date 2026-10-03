"""
领用包序列化器
"""
from decimal import Decimal

from rest_framework import serializers

from apps.warehouse.models import Goods
from .models import Kit, KitApplication, KitApplicationItem, KitItem, KitVersion


class KitItemSerializer(serializers.ModelSerializer):
    """领用包物资项序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    goods_code = serializers.CharField(source='goods.code', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    substitute_names = serializers.SerializerMethodField()

    class Meta:
        model = KitItem
        fields = [
            'id', 'goods', 'goods_name', 'goods_code',
            'role', 'role_display', 'quantity', 'required',
            'substitutes', 'substitute_names', 'sort_order'
        ]

    def get_substitute_names(self, obj):
        return [goods.name for goods in obj.substitutes.all()]


class KitItemInputSerializer(serializers.Serializer):
    """物资项录入序列化器"""
    goods = serializers.IntegerField(required=True, error_messages={
        'required': '请选择货物',
    })
    role = serializers.ChoiceField(choices=KitItem.ROLE_CHOICES, default='accessory')
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2,
        min_value=Decimal('0.01'),
        error_messages={'min_value': '每套数量必须大于0'},
    )
    required = serializers.BooleanField(default=True)
    substitutes = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    sort_order = serializers.IntegerField(default=0, min_value=0)

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value).exists():
            raise serializers.ValidationError('货物不存在')
        return value

    def validate(self, data):
        substitutes = data.get('substitutes', [])
        if data['goods'] in substitutes:
            raise serializers.ValidationError('替代物资不能与主选货物相同')
        if len(set(substitutes)) != len(substitutes):
            raise serializers.ValidationError('替代物资存在重复')
        for goods_id in substitutes:
            if not Goods.objects.filter(pk=goods_id).exists():
                raise serializers.ValidationError(f'替代货物 {goods_id} 不存在')
        return data


class KitVersionSerializer(serializers.ModelSerializer):
    """领用包版本序列化器"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    items = KitItemSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = KitVersion
        fields = [
            'id', 'kit', 'version_no', 'status', 'status_display',
            'note', 'items', 'published_at',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class KitVersionBriefSerializer(serializers.ModelSerializer):
    """版本简要序列化器（用于包详情嵌套）"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = KitVersion
        fields = ['id', 'version_no', 'status', 'status_display', 'note', 'item_count', 'published_at']

    def get_item_count(self, obj):
        return obj.items.count()


class KitSerializer(serializers.ModelSerializer):
    """领用包序列化器"""
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    current_version = KitVersionBriefSerializer(read_only=True)

    class Meta:
        model = Kit
        fields = [
            'id', 'name', 'code', 'description', 'is_active',
            'current_version', 'created_by', 'created_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class KitCreateSerializer(serializers.Serializer):
    """领用包创建序列化器（可同时携带首个版本的物资项）"""
    name = serializers.CharField(min_length=1, max_length=50, required=True, error_messages={
        'required': '请输入包名称',
        'blank': '包名称不能为空',
        'max_length': '包名称最多50个字',
    })
    code = serializers.CharField(min_length=1, max_length=50, required=True, error_messages={
        'required': '请输入包编码',
        'blank': '包编码不能为空',
        'max_length': '包编码最多50个字',
    })
    description = serializers.CharField(required=False, allow_blank=True, default='')
    items = serializers.ListField(
        child=KitItemInputSerializer(), required=False, default=list
    )

    def validate_name(self, value):
        instance = self.context.get('instance')
        queryset = Kit.objects.filter(name=value)
        if instance:
            queryset = queryset.exclude(pk=instance.pk)
        if queryset.exists():
            raise serializers.ValidationError('包名称已存在')
        return value

    def validate_code(self, value):
        instance = self.context.get('instance')
        queryset = Kit.objects.filter(code=value)
        if instance:
            queryset = queryset.exclude(pk=instance.pk)
        if queryset.exists():
            raise serializers.ValidationError('包编码已存在')
        return value


class KitUpdateSerializer(serializers.Serializer):
    """领用包更新序列化器"""
    name = serializers.CharField(min_length=1, max_length=50, required=True, error_messages={
        'required': '请输入包名称',
        'blank': '包名称不能为空',
        'max_length': '包名称最多50个字',
    })
    description = serializers.CharField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(default=True)

    def validate_name(self, value):
        instance = self.context.get('instance')
        queryset = Kit.objects.filter(name=value)
        if instance:
            queryset = queryset.exclude(pk=instance.pk)
        if queryset.exists():
            raise serializers.ValidationError('包名称已存在')
        return value


class KitVersionCreateSerializer(serializers.Serializer):
    """版本创建序列化器：携带物资项则按之建立，否则复制当前已发布版本"""
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    items = serializers.ListField(
        child=KitItemInputSerializer(), required=False, default=None, allow_null=True
    )


class SubstituteChoiceSerializer(serializers.Serializer):
    """替代选择序列化器：可替代项必须记录选择依据"""
    kit_item = serializers.IntegerField(required=True, error_messages={
        'required': '请指定要替代的物资项',
    })
    substitute_goods = serializers.IntegerField(required=True, error_messages={
        'required': '请选择替代货物',
    })
    reason = serializers.CharField(max_length=200, required=True, allow_blank=False, error_messages={
        'required': '选择替代物资必须记录选择依据',
        'blank': '选择依据不能为空',
    })


class KitApplicationCreateSerializer(serializers.Serializer):
    """领用申请创建序列化器"""
    kit = serializers.IntegerField(required=True, error_messages={
        'required': '请选择领用包',
    })
    receiver = serializers.CharField(min_length=1, max_length=100, required=True, error_messages={
        'required': '请输入领用人',
        'blank': '领用人不能为空',
    })
    receiver_dept = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    remark = serializers.CharField(required=False, allow_blank=True, default='')
    substitute_choices = serializers.ListField(
        child=SubstituteChoiceSerializer(), required=False, default=list
    )

    def validate_kit(self, value):
        if not Kit.objects.filter(pk=value).exists():
            raise serializers.ValidationError('领用包不存在')
        return value


class KitApplicationItemSerializer(serializers.ModelSerializer):
    """申请明细序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    goods_code = serializers.CharField(source='goods.code', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = KitApplicationItem
        fields = [
            'id', 'kit_item', 'goods', 'goods_name', 'goods_code',
            'role', 'role_display', 'required',
            'is_substitute', 'substitute_reason',
            'requested_quantity', 'reserved_quantity',
            'issued_quantity', 'returned_quantity',
            'status', 'status_display', 'created_at'
        ]


class KitApplicationSerializer(serializers.ModelSerializer):
    """领用申请序列化器"""
    kit_name = serializers.CharField(source='kit_version.kit.name', read_only=True)
    kit_code = serializers.CharField(source='kit_version.kit.code', read_only=True)
    version_no = serializers.IntegerField(source='kit_version.version_no', read_only=True)
    applicant_name = serializers.CharField(source='applicant.username', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    items = KitApplicationItemSerializer(many=True, read_only=True)

    class Meta:
        model = KitApplication
        fields = [
            'id', 'kit_version', 'kit_name', 'kit_code', 'version_no',
            'applicant', 'applicant_name', 'receiver', 'receiver_dept',
            'status', 'status_display', 'shortage_detail', 'remark',
            'items', 'approved_by', 'approved_by_name', 'approved_at',
            'issued_at', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class KitApplicationReturnSerializer(serializers.Serializer):
    """退回明细序列化器"""
    item = serializers.IntegerField(required=True, error_messages={
        'required': '请指定退回的物资项',
    })
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2,
        min_value=Decimal('0.01'),
        error_messages={'min_value': '退回数量必须大于0'},
    )
