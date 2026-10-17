"""S1 · T1 — Modelo de usuario por correo (AUTH_USER_MODEL = accounts.User)."""

import uuid

import pytest
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_auth_user_model_apunta_a_accounts():
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert User._meta.label == "accounts.User"


def test_el_login_es_el_correo():
    assert User.USERNAME_FIELD == "email"
    assert User.REQUIRED_FIELDS == []


def test_create_user_guarda_correo_en_minusculas():
    user = User.objects.create_user("Maria.Perez@Example.COM", "clave-segura-123")
    assert user.email == "maria.perez@example.com"


def test_create_user_exige_correo():
    with pytest.raises(ValueError):
        User.objects.create_user("", "clave-segura-123")


def test_la_clave_se_guarda_con_hash():
    user = User.objects.create_user("a@example.com", "clave-segura-123")
    assert user.password != "clave-segura-123"
    assert user.check_password("clave-segura-123")


def test_create_user_sin_clave_deja_clave_inutilizable():
    user = User.objects.create_user("a@example.com")
    assert not user.has_usable_password()


def test_el_correo_es_unico_sin_distinguir_mayusculas():
    User.objects.create_user("a@example.com", "clave-segura-123")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("A@EXAMPLE.com", "otra-clave-456")


def test_la_clave_primaria_es_uuid():
    user = User.objects.create_user("a@example.com", "clave-segura-123")
    assert isinstance(user.pk, uuid.UUID)


def test_usuario_normal_no_es_staff_ni_superusuario():
    user = User.objects.create_user("a@example.com", "clave-segura-123")
    assert user.is_active
    assert not user.is_staff
    assert not user.is_superuser


def test_create_superuser_activa_los_permisos():
    admin = User.objects.create_superuser("root@example.com", "clave-segura-123")
    assert admin.is_staff
    assert admin.is_superuser


def test_str_devuelve_el_correo():
    user = User.objects.create_user("a@example.com", "clave-segura-123")
    assert str(user) == "a@example.com"


def test_authenticate_por_correo():
    User.objects.create_user("a@example.com", "clave-segura-123")
    assert authenticate(username="a@example.com", password="clave-segura-123")
    assert authenticate(username="a@example.com", password="mala") is None
