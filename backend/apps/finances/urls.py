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
        "reports/delinquency/",
        views.DelinquencyReportView.as_view(),
        name="report-delinquency",
    ),
]
