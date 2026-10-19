"""Configuración compartida de pytest."""

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def hasher_rapido(settings):
    """En pruebas se usa un hasher barato; en producción rige el configurado."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def cache_limpia():
    """Los contadores de intentos de login viven en caché: cada prueba parte de cero."""
    cache.clear()
    yield
    cache.clear()
