"""Producción (VPS, detrás de Nginx). Falla al arrancar si falta algo crítico."""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env_list

DEBUG = False

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY es obligatoria en producción.")

if not os.environ.get("POSTGRES_DB"):
    raise ImproperlyConfigured(
        "POSTGRES_DB es obligatoria en producción (no se usa SQLite)."
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS es obligatoria en producción.")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Nginx termina el HTTPS y reenvía a Gunicorn por HTTP.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# HSTS y el resto de endurecimiento se completan en la Fase 4 (manage.py check --deploy).
TRUSTED_PROXY_COUNT = 1
