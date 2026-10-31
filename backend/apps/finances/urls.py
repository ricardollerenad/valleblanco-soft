from django.urls import path

from . import views

urlpatterns = [
    path("fee-rates/", views.FeeRateListCreateView.as_view(), name="fee-rate-list"),
    path("charges/", views.ChargeListCreateView.as_view(), name="charge-list"),
    path(
        "charges/generate/",
        views.ChargeGenerateView.as_view(),
        name="charge-generate",
    ),
    path(
        "charges/<uuid:pk>/void/",
        views.ChargeVoidView.as_view(),
        name="charge-void",
    ),
    path(
        "units/<uuid:pk>/statement/",
        views.UnitStatementView.as_view(),
        name="unit-statement",
    ),
    path(
        "units/<uuid:pk>/statement/pdf/",
        views.UnitStatementPdfView.as_view(),
        name="unit-statement-pdf",
    ),
    path("payments/", views.PaymentListCreateView.as_view(), name="payment-list"),
    path(
        "payments/<uuid:pk>/",
        views.PaymentDetailView.as_view(),
        name="payment-detail",
    ),
    path(
        "payments/<uuid:pk>/voucher/",
        views.PaymentVoucherView.as_view(),
        name="payment-voucher",
    ),
    path(
        "payments/<uuid:pk>/receipt/pdf/",
        views.PaymentReceiptPdfView.as_view(),
        name="payment-receipt-pdf",
    ),
    path(
        "payments/<uuid:pk>/approve/",
        views.PaymentApproveView.as_view(),
        name="payment-approve",
    ),
    path(
        "payments/<uuid:pk>/reject/",
        views.PaymentRejectView.as_view(),
        name="payment-reject",
    ),
    path(
        "payments/<uuid:pk>/void/",
        views.PaymentVoidView.as_view(),
        name="payment-void",
    ),
    path(
        "reports/delinquency/",
        views.DelinquencyReportView.as_view(),
        name="report-delinquency",
    ),
]
