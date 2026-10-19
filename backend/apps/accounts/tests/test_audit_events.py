"""T6: la API de cuentas deja rastro en AuditLog."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.services import accept_invitation, invite_user
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "Cielo-Azul-9347-Zq"
LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"
INVITE_URL = "/api/v1/users/"
ACCEPT_URL = "/api/v1/auth/invitations/accept/"


@pytest.fixture
def admin():
    return User.objects.create_superuser("admin@valleblanco.pe", PASSWORD)


def actions():
    return list(
        AuditLog.objects.order_by("created_at").values_list("action", flat=True)
    )


# --- Invitaciones --------------------------------------------------------


def test_invitar_por_api_queda_registrado(admin):
    client = APIClient()
    client.force_authenticate(admin)

    response = client.post(INVITE_URL, {"email": "nuevo@valleblanco.pe"})

    entry = AuditLog.objects.get(action="INVITATION_CREATED")
    invitation = User.objects.get(email="nuevo@valleblanco.pe").invitations.get()
    assert entry.entity_type == "Invitation"
    assert entry.entity_id == str(invitation.pk)
    assert entry.actor_id == str(admin.pk)
    assert entry.actor_label == "admin@valleblanco.pe"
    assert entry.changes["email"] == "nuevo@valleblanco.pe"
    assert entry.ip_address == "127.0.0.1"
    # el token en claro jamás se guarda en la auditoría
    assert response.json()["invitation"]["token"] not in str(entry.changes)


def test_invitar_desde_el_servicio_sin_ip_ni_actor():
    invite_user("nuevo@valleblanco.pe")

    entry = AuditLog.objects.get(action="INVITATION_CREATED")
    assert entry.actor_id == ""
    assert entry.ip_address is None


def test_aceptar_invitacion_queda_registrado():
    result = invite_user("nuevo@valleblanco.pe")

    APIClient().post(ACCEPT_URL, {"token": result.token, "password": PASSWORD})

    entry = AuditLog.objects.get(action="INVITATION_ACCEPTED")
    assert entry.entity_type == "Invitation"
    assert entry.entity_id == str(result.invitation.pk)
    assert entry.actor_label == "nuevo@valleblanco.pe"
    assert entry.ip_address == "127.0.0.1"
    assert PASSWORD not in str(entry.changes)


def test_aceptar_con_clave_debil_no_registra_aceptacion():
    result = invite_user("nuevo@valleblanco.pe")

    APIClient().post(ACCEPT_URL, {"token": result.token, "password": "123"})

    assert not AuditLog.objects.filter(action="INVITATION_ACCEPTED").exists()


def test_aceptar_desde_el_servicio_sin_ip():
    result = invite_user("nuevo@valleblanco.pe")

    accept_invitation(result.token, PASSWORD)

    assert AuditLog.objects.get(action="INVITATION_ACCEPTED").ip_address is None


# --- Accesos -------------------------------------------------------------


def test_login_correcto_queda_registrado():
    user = User.objects.create_user("vecino@valleblanco.pe", PASSWORD)

    APIClient().post(
        LOGIN_URL, {"email": "vecino@valleblanco.pe", "password": PASSWORD}
    )

    entry = AuditLog.objects.get(action="LOGIN_SUCCEEDED")
    assert entry.entity_type == "User"
    assert entry.entity_id == str(user.pk)
    assert entry.actor_id == str(user.pk)
    assert entry.ip_address == "127.0.0.1"


def test_login_fallido_registra_el_correo_pero_nunca_la_clave():
    User.objects.create_user("vecino@valleblanco.pe", PASSWORD)

    APIClient().post(
        LOGIN_URL, {"email": "Vecino@ValleBlanco.pe", "password": "clave-equivocada"}
    )

    entry = AuditLog.objects.get(action="LOGIN_FAILED")
    assert entry.actor_id == ""
    assert entry.actor_label == "vecino@valleblanco.pe"
    assert "clave-equivocada" not in str(entry.changes)
    assert not AuditLog.objects.filter(action="LOGIN_SUCCEEDED").exists()


def test_login_fallido_con_correo_inexistente_tambien_se_registra():
    APIClient().post(LOGIN_URL, {"email": "nadie@valleblanco.pe", "password": "x"})
    assert AuditLog.objects.filter(action="LOGIN_FAILED").count() == 1


def test_el_bloqueo_se_registra_una_sola_vez_por_ventana():
    User.objects.create_user("vecino@valleblanco.pe", PASSWORD)
    client = APIClient()
    for _ in range(5):
        client.post(LOGIN_URL, {"email": "vecino@valleblanco.pe", "password": "mal"})
    for _ in range(3):
        response = client.post(
            LOGIN_URL, {"email": "vecino@valleblanco.pe", "password": PASSWORD}
        )
        assert response.status_code == 429

    assert actions().count("LOGIN_FAILED") == 5
    assert actions().count("LOGIN_BLOCKED") == 1
    assert actions().count("LOGIN_SUCCEEDED") == 0


def test_logout_queda_registrado():
    user = User.objects.create_user("vecino@valleblanco.pe", PASSWORD)
    client = APIClient()
    client.force_authenticate(user)

    client.post(LOGOUT_URL)

    entry = AuditLog.objects.get(action="LOGOUT")
    assert entry.actor_id == str(user.pk)
    assert entry.entity_id == str(user.pk)
    assert entry.ip_address == "127.0.0.1"
