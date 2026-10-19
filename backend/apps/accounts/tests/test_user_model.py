"""T1: modelo de usuario por correo (login, unicidad, clave y UUID)."""

import uuid

import pytest
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db

User = get_user_model()
STRONG = "Cielo-Azul-9347-Zq"


def test_auth_user_model_apunta_a_accounts():
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert User._meta.app_label == "accounts"


def test_el_login_es_el_correo():
    assert User.USERNAME_FIELD == "email"


def test_authenticate_por_correo():
    user = User.objects.create_user("vecino@valleblanco.pe", STRONG)

    assert authenticate(username="vecino@valleblanco.pe", password=STRONG) == user
    assert authenticate(username="vecino@valleblanco.pe", password="otra") is None


def test_create_user_exige_correo():
    with pytest.raises(ValueError):
        User.objects.create_user("", STRONG)


def test_create_user_guarda_correo_en_minusculas():
    user = User.objects.create_user("Vecino@ValleBlanco.PE", STRONG)

    assert user.email == "vecino@valleblanco.pe"


def test_el_correo_es_unico_sin_distinguir_mayusculas():
    User.objects.create_user("vecino@valleblanco.pe", STRONG)

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("VECINO@valleblanco.pe", STRONG)


def test_create_user_sin_clave_deja_clave_inutilizable():
    user = User.objects.create_user("vecino@valleblanco.pe")

    assert not user.has_usable_password()


def test_la_clave_se_guarda_con_hash():
    user = User.objects.create_user("vecino@valleblanco.pe", STRONG)

    assert user.password != STRONG
    assert STRONG not in user.password
    assert user.check_password(STRONG)


def test_create_superuser_activa_los_permisos():
    admin = User.objects.create_superuser("admin@valleblanco.pe", STRONG)

    assert admin.is_staff
    assert admin.is_superuser
    assert admin.is_active


def test_usuario_normal_no_es_staff_ni_superusuario():
    user = User.objects.create_user("vecino@valleblanco.pe", STRONG)

    assert not user.is_staff
    assert not user.is_superuser
    assert user.is_active


def test_la_clave_primaria_es_uuid():
    user = User.objects.create_user("vecino@valleblanco.pe", STRONG)

    assert isinstance(user.pk, uuid.UUID)


def test_str_devuelve_el_correo():
    user = User.objects.create_user("vecino@valleblanco.pe", STRONG)

    assert str(user) == "vecino@valleblanco.pe"
