# RED: sin rutas todavía. La ruta /health/ se añade en la fase GREEN.
from django.urls import path

from .views import HealthView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
]
