"""Rutas raíz del proyecto. Cada app aporta las suyas bajo /api/v1/."""

from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("apps.core.urls")),
]
