from django.urls import path

from . import views

urlpatterns = [
    path("units/", views.UnitListView.as_view(), name="unit-list"),
    path("units/<uuid:pk>/", views.UnitDetailView.as_view(), name="unit-detail"),
    path(
        "units/<uuid:pk>/occupancies/",
        views.UnitOccupanciesView.as_view(),
        name="unit-occupancies",
    ),
    path(
        "occupancies/<uuid:pk>/",
        views.OccupancyDetailView.as_view(),
        name="occupancy-detail",
    ),
    path("people/", views.PersonListCreateView.as_view(), name="person-list"),
    path("people/<uuid:pk>/", views.PersonDetailView.as_view(), name="person-detail"),
    path(
        "people/<uuid:pk>/invite/",
        views.PersonInviteView.as_view(),
        name="person-invite",
    ),
    path(
        "role-assignments/",
        views.RoleAssignmentListCreateView.as_view(),
        name="role-assignment-list",
    ),
    path(
        "role-assignments/<uuid:pk>/",
        views.RoleAssignmentDetailView.as_view(),
        name="role-assignment-detail",
    ),
]
