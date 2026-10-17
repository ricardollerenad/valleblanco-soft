"""Configuración compartida de pytest."""

import pytest


@pytest.fixture(autouse=True)
def hasher_rapido(settings):
    """En pruebas se usa un hasher barato; en producción rige el configurado."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
