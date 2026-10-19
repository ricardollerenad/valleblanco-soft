"""T5: permiso de administrador, invitar usuarios y aceptar invitaciones por API."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Invitation
from apps.accounts.services import invite_user

pytestmark = pytest.mark.django_db

User = get_user_model()
INVITE_URL = "/api/v1/users/"
ACCEPT_URL = "/api/v1/auth/invitations/accept/"
STRONG = "Cielo-Azul-9347-Zq"


@pytest.fixture
def admin():
    return User.objects.create_superuser("admin@valleblanco.pe", STRONG)


@pytest.fixture
def admin_client(admin):
    client = APIClient()
    client.force_authenticate(admin)
    return client


@pytest.fixture
def resident_client():
    user = User.objects.create_user("vecino@valleblanco.pe", STRONG)
    client = APIClient()
    client.force_authenticate(user)
    return client


# --- Quién puede invitar -------------------------------------------------


def test_anonimo_no_puede_invitar():
    response = APIClient().post(INVITE_URL, {"email": "nuevo@valleblanco.pe"})
    assert response.status_code in (401, 403)
    assert not User.objects.filter(email="nuevo@valleblanco.pe").exists()


def test_residente_no_puede_invitar(resident_client):
    response = resident_client.post(INVITE_URL, {"email": "nuevo@valleblanco.pe"})
    assert response.status_code == 403
    assert not User.objects.filter(email="nuevo@valleblanco.pe").exists()


# --- Invitar -------------------------------------------------------------


def test_admin_invita_y_ve_el_enlace(admin_client, admin):
    response = admin_client.post(INVITE_URL, {"email": "nuevo@valleblanco.pe"})

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "nuevo@valleblanco.pe"
    token = data["invitation"]["token"]
    assert data["invitation"]["path"] == f"/invitacion/{token}"
    assert data["invitation"]["expires_at"]
    assert response["Cache-Control"] == "no-store"

    user = User.objects.get(email="nuevo@valleblanco.pe")
    assert user.is_active is False
    assert user.invitations.get().invited_by == admin


def test_el_correo_se_normaliza(admin_client):
    response = admin_client.post(INVITE_URL, {"email": "  Nuevo@ValleBlanco.PE "})
    assert response.status_code == 201
    assert response.json()["email"] == "nuevo@valleblanco.pe"


def test_correo_invalido_devuelve_400(admin_client):
    response = admin_client.post(INVITE_URL, {"email": "no-es-correo"})
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "email" in response.json()["errors"]


def test_usuario_ya_activo_devuelve_409(admin_client, resident_client):
    response = admin_client.post(INVITE_URL, {"email": "vecino@valleblanco.pe"})
    assert response.status_code == 409
    assert response.json()["code"] == "user_already_active"


def test_reinvitar_invalida_el_enlace_anterior(admin_client):
    first = admin_client.post(INVITE_URL, {"email": "nuevo@valleblanco.pe"}).json()
    second = admin_client.post(INVITE_URL, {"email": "nuevo@valleblanco.pe"}).json()
    assert first["invitation"]["token"] != second["invitation"]["token"]

    old = APIClient().post(
        ACCEPT_URL, {"token": first["invitation"]["token"], "password": STRONG}
    )
    assert old.status_code == 400
    assert old.json()["code"] == "invitation_expired"


# --- Aceptar -------------------------------------------------------------


def test_aceptar_activa_la_cuenta_sin_necesidad_de_sesion():
    result = invite_user("nuevo@valleblanco.pe")

    response = APIClient().post(ACCEPT_URL, {"token": result.token, "password": STRONG})

    assert response.status_code == 200
    user = User.objects.get(email="nuevo@valleblanco.pe")
    assert user.is_active is True
    assert user.check_password(STRONG)


def test_token_inexistente_devuelve_400():
    response = APIClient().post(ACCEPT_URL, {"token": "falso", "password": STRONG})
    assert response.status_code == 400
    assert response.json()["code"] == "invitation_invalid"


def test_token_no_se_puede_reutilizar():
    result = invite_user("nuevo@valleblanco.pe")
    client = APIClient()
    client.post(ACCEPT_URL, {"token": result.token, "password": STRONG})

    response = client.post(ACCEPT_URL, {"token": result.token, "password": STRONG})

    assert response.status_code == 400
    assert response.json()["code"] == "invitation_used"


def test_invitacion_vencida_devuelve_400():
    result = invite_user("nuevo@valleblanco.pe")
    Invitation.objects.filter(pk=result.invitation.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    response = APIClient().post(ACCEPT_URL, {"token": result.token, "password": STRONG})

    assert response.status_code == 400
    assert response.json()["code"] == "invitation_expired"


def test_contrasena_debil_no_consume_la_invitacion():
    result = invite_user("nuevo@valleblanco.pe")

    response = APIClient().post(ACCEPT_URL, {"token": result.token, "password": "123"})

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "password" in response.json()["errors"]
    result.invitation.refresh_from_db()
    assert result.invitation.accepted_at is None
    assert User.objects.get(email="nuevo@valleblanco.pe").is_active is False


def test_faltan_campos_devuelve_400():
    response = APIClient().post(ACCEPT_URL, {})
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
