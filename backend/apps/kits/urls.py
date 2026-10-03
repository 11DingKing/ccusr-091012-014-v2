"""
领用包URL配置
"""
from django.urls import path
from .views import (
    KitListView, KitDetailView,
    KitVersionListView, KitVersionDetailView, KitVersionPublishView,
    KitApplicationListView, KitApplicationDetailView,
    KitApplicationApproveView, KitApplicationRejectView,
    KitApplicationCancelView, KitApplicationIssueView,
    KitApplicationRecheckView, KitApplicationReturnView,
)

urlpatterns = [
    # 领用包定义
    path('kits/', KitListView.as_view(), name='kit-list'),
    path('kits/<int:pk>/', KitDetailView.as_view(), name='kit-detail'),
    path('kits/<int:pk>/versions/', KitVersionListView.as_view(), name='kit-version-list'),

    # 领用包版本
    path('kit-versions/<int:pk>/', KitVersionDetailView.as_view(), name='kit-version-detail'),
    path('kit-versions/<int:pk>/publish/', KitVersionPublishView.as_view(), name='kit-version-publish'),

    # 领用申请
    path('kit-applications/', KitApplicationListView.as_view(), name='kit-application-list'),
    path('kit-applications/<int:pk>/', KitApplicationDetailView.as_view(), name='kit-application-detail'),
    path('kit-applications/<int:pk>/approve/', KitApplicationApproveView.as_view(), name='kit-application-approve'),
    path('kit-applications/<int:pk>/reject/', KitApplicationRejectView.as_view(), name='kit-application-reject'),
    path('kit-applications/<int:pk>/cancel/', KitApplicationCancelView.as_view(), name='kit-application-cancel'),
    path('kit-applications/<int:pk>/issue/', KitApplicationIssueView.as_view(), name='kit-application-issue'),
    path('kit-applications/<int:pk>/recheck/', KitApplicationRecheckView.as_view(), name='kit-application-recheck'),
    path('kit-applications/<int:pk>/return/', KitApplicationReturnView.as_view(), name='kit-application-return'),
]
