"""
领用包模型

办案人员按固定组合（主机、配件、封存介质）整套领用物资：
- Kit / KitVersion / KitItem 维护有版本的领用包定义，发布后不可修改，升级即新版本；
- KitApplication / KitApplicationItem 在申请时把包版本展开成具体货物的库存占用，
  必需项全部可用才进入审批，可替代项必须记录选择依据。
"""
from django.db import models
from apps.authentication.models import User
from apps.warehouse.models import Goods


class Kit(models.Model):
    """领用包定义"""
    name = models.CharField('包名称', max_length=50, unique=True)
    code = models.CharField('包编码', max_length=50, unique=True)
    description = models.TextField('用途说明', blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_kits', verbose_name='创建人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'kit'
        verbose_name = '领用包'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def current_version(self):
        """当前已发布版本"""
        return self.versions.filter(status='published').order_by('-version_no').first()


class KitVersion(models.Model):
    """领用包版本（发布后不可修改，升级通过新建版本完成）"""
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('published', '已发布'),
        ('retired', '已停用'),
    ]

    kit = models.ForeignKey(
        Kit, on_delete=models.CASCADE,
        related_name='versions', verbose_name='所属领用包'
    )
    version_no = models.PositiveIntegerField('版本号')
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='draft')
    note = models.CharField('版本说明', max_length=200, blank=True)
    published_at = models.DateTimeField('发布时间', null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_kit_versions', verbose_name='创建人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'kit_version'
        verbose_name = '领用包版本'
        verbose_name_plural = verbose_name
        ordering = ['-version_no']
        unique_together = ['kit', 'version_no']

    def __str__(self):
        return f"{self.kit.name} v{self.version_no}"


class KitItem(models.Model):
    """领用包版本内的物资项"""
    ROLE_CHOICES = [
        ('host', '主机'),
        ('accessory', '配件'),
        ('media', '封存介质'),
    ]

    version = models.ForeignKey(
        KitVersion, on_delete=models.CASCADE,
        related_name='items', verbose_name='所属版本'
    )
    goods = models.ForeignKey(
        Goods, on_delete=models.PROTECT,
        related_name='kit_items', verbose_name='主选货物'
    )
    role = models.CharField('组合角色', max_length=20, choices=ROLE_CHOICES, default='accessory')
    quantity = models.DecimalField('每套数量', max_digits=12, decimal_places=2)
    required = models.BooleanField('是否必需', default=True)
    substitutes = models.ManyToManyField(
        Goods, blank=True,
        related_name='substitute_in_kit_items', verbose_name='可替代货物'
    )
    sort_order = models.PositiveIntegerField('排序', default=0)

    class Meta:
        db_table = 'kit_item'
        verbose_name = '领用包物资项'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f"{self.version} - {self.goods.name} x {self.quantity}"


class KitApplication(models.Model):
    """领用申请（按申请时锁定的包版本展开）"""
    STATUS_CHOICES = [
        ('shortage', '缺货待补'),
        ('pending', '待审批'),
        ('approved', '已批准'),
        ('rejected', '已拒绝'),
        ('issued', '已发放'),
        ('partially_returned', '部分退回'),
        ('returned', '已退回'),
        ('cancelled', '已取消'),
    ]

    kit_version = models.ForeignKey(
        KitVersion, on_delete=models.PROTECT,
        related_name='applications', verbose_name='锁定的包版本'
    )
    applicant = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='kit_applications', verbose_name='申请人'
    )
    receiver = models.CharField('领用人', max_length=100)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    status = models.CharField('状态', max_length=30, choices=STATUS_CHOICES, default='pending')
    shortage_detail = models.JSONField('缺货明细', default=list, blank=True)
    remark = models.TextField('备注', blank=True)
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_kit_applications', verbose_name='审批人'
    )
    approved_at = models.DateTimeField('审批时间', null=True, blank=True)
    issued_at = models.DateTimeField('发放时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'kit_application'
        verbose_name = '领用申请'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.kit_version} - {self.receiver} - {self.get_status_display()}"


class KitApplicationItem(models.Model):
    """领用申请明细：包版本展开后的具体货物占用"""
    STATUS_CHOICES = [
        ('reserved', '已占用'),
        ('issued', '已发放'),
        ('partially_returned', '部分退回'),
        ('returned', '已退回'),
        ('released', '已释放'),
    ]

    application = models.ForeignKey(
        KitApplication, on_delete=models.CASCADE,
        related_name='items', verbose_name='所属申请'
    )
    kit_item = models.ForeignKey(
        KitItem, on_delete=models.SET_NULL, null=True,
        related_name='application_items', verbose_name='来源定义项'
    )
    goods = models.ForeignKey(
        Goods, on_delete=models.PROTECT,
        related_name='kit_application_items', verbose_name='实际占用货物'
    )
    role = models.CharField('组合角色', max_length=20, choices=KitItem.ROLE_CHOICES)
    required = models.BooleanField('是否必需')
    is_substitute = models.BooleanField('是否替代物资', default=False)
    substitute_reason = models.CharField('替代选择依据', max_length=200, blank=True)
    requested_quantity = models.DecimalField('申请数量', max_digits=12, decimal_places=2)
    reserved_quantity = models.DecimalField('占用数量', max_digits=12, decimal_places=2, default=0)
    issued_quantity = models.DecimalField('发放数量', max_digits=12, decimal_places=2, default=0)
    returned_quantity = models.DecimalField('已退数量', max_digits=12, decimal_places=2, default=0)
    status = models.CharField('状态', max_length=30, choices=STATUS_CHOICES, default='reserved')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'kit_application_item'
        verbose_name = '领用申请明细'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return f"{self.application_id} - {self.goods.name} x {self.requested_quantity}"
