"""
仓库管理URL配置
"""
from django.urls import path
from .views import (
    UnitListView, UnitDetailView, UnitBatchDeleteView, UnitAllView,
    CategoryListView, CategoryDetailView, CategoryBatchDeleteView, CategoryAllView,
    VarietyListView, VarietyDetailView, VarietyBatchDeleteView,
    VarietyTemplateView, VarietyImportView,
    DashboardView, GoodsListView, StockInListView, StockOutListView,
    WarningListView, ApprovalListView,
    KitTemplateListView, KitTemplateDetailView,
    KitVersionView, KitVersionDetailView,
    KitRequestPreviewView, KitRequestListView, KitRequestDetailView,
    KitRequestApproveView, KitRequestCancelView,
    KitRequestIssueView, KitRequestReturnView,
    StockFreezeView, StockFreezeListView,
)

urlpatterns = [
    # 仪表盘
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # 单位管理
    path('units/', UnitListView.as_view(), name='unit-list'),
    path('units/all/', UnitAllView.as_view(), name='unit-all'),
    path('units/batch-delete/', UnitBatchDeleteView.as_view(), name='unit-batch-delete'),
    path('units/<int:pk>/', UnitDetailView.as_view(), name='unit-detail'),

    # 品类管理
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('categories/all/', CategoryAllView.as_view(), name='category-all'),
    path('categories/batch-delete/', CategoryBatchDeleteView.as_view(), name='category-batch-delete'),
    path('categories/<int:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    # 品种管理
    path('varieties/', VarietyListView.as_view(), name='variety-list'),
    path('varieties/batch-delete/', VarietyBatchDeleteView.as_view(), name='variety-batch-delete'),
    path('varieties/template/', VarietyTemplateView.as_view(), name='variety-template'),
    path('varieties/import/', VarietyImportView.as_view(), name='variety-import'),
    path('varieties/<int:pk>/', VarietyDetailView.as_view(), name='variety-detail'),

    # 货物管理
    path('goods/', GoodsListView.as_view(), name='goods-list'),

    # 入库管理
    path('stock-in/', StockInListView.as_view(), name='stock-in-list'),

    # 出库管理
    path('stock-out/', StockOutListView.as_view(), name='stock-out-list'),

    # 预警管理
    path('warnings/', WarningListView.as_view(), name='warning-list'),

    # 审批管理
    path('approvals/', ApprovalListView.as_view(), name='approval-list'),

    # 物资冻结/解冻
    path('stock-freeze/<str:freeze_type>/', StockFreezeView.as_view(), name='stock-freeze'),
    path('stock-freeze-records/', StockFreezeListView.as_view(), name='stock-freeze-list'),

    # 领用包定义与版本
    path('kits/', KitTemplateListView.as_view(), name='kit-template-list'),
    path('kits/<int:pk>/', KitTemplateDetailView.as_view(), name='kit-template-detail'),
    path('kits/<int:template_pk>/versions/', KitVersionView.as_view(), name='kit-version-list'),
    path('kit-versions/<int:pk>/', KitVersionDetailView.as_view(), name='kit-version-detail'),
    path('kit-versions/<int:pk>/activate/', KitVersionDetailView.as_view(),
         {'activate': True}, name='kit-version-activate'),

    # 领用申请
    path('kit-requests/preview/', KitRequestPreviewView.as_view(), name='kit-request-preview'),
    path('kit-requests/', KitRequestListView.as_view(), name='kit-request-list'),
    path('kit-requests/<int:pk>/', KitRequestDetailView.as_view(), name='kit-request-detail'),
    path('kit-requests/<int:pk>/approve/', KitRequestApproveView.as_view(), name='kit-request-approve'),
    path('kit-requests/<int:pk>/cancel/', KitRequestCancelView.as_view(), name='kit-request-cancel'),
    path('kit-requests/<int:pk>/issue/', KitRequestIssueView.as_view(), name='kit-request-issue'),
    path('kit-requests/<int:pk>/return/', KitRequestReturnView.as_view(), name='kit-request-return'),
]
