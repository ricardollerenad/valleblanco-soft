"""S1 · T3 — Aceptar una invitación: crear la contraseña y activar la cuenta."""

from datetime import timedelta

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import Invitation
from apps.accounts.services import (
    InvitationAlreadyUsedError,
    InvitationError,
    InvitationExpiredError,
    InvitationNotFoundError,
    accept_invitation,
    invite_user,
)

User = get_user_model()

pytestmark = pytest.mark.django_db

CLAVE_VALIDA = "Cerro-Blanco-2026"


@pytest.fixture
def admin():
    return User.objects.create_superuser("admin@example.com", "clave-segura-123")


@pytest.fixture
def invitacion(admin):
    return invite_user("vecino@example.com", invited_by=admin)


def test_aceptar_activa_la_cuenta_y_fija_la_clave(invitacion):
    user = accept_invitation(invitacion.token, CLAVE_VALIDA)
    user.refresh_from_db()
    assert user.is_active
    assert user.has_usable_password()
    assert authenticate(username="vecino@example.com", password=CLAVE_VALIDA)


def test_aceptar_marca_la_invitacion_como_usada(invitacion):
    accept_invitation(invitacion.token, CLAVE_VALIDA)
    invitation = Invitation.objects.get(pk=invitacion.invitation.pk)
    assert invitation.accepted_at is not None
    assert not invitation.is_usable


def test_token_inexistente_se_rechaza():
    with pytest.raises(InvitationNotFoundError):
        accept_invitation("token-que-no-existe", CLAVE_VALIDA)


def test_token_vacio_se_rechaza():
    with pytest.raises(InvitationNotFoundError):
        accept_invitation("", CLAVE_VALIDA)


def test_token_vencido_se_rechaza_y_no_activa_nada(invitacion):
    Invitation.objects.filter(pk=invitacion.invitation.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    with pytest.raises(InvitationExpiredError):
        accept_invitation(invitacion.token, CLAVE_VALIDA)
    invitacion.user.refresh_from_db()
    assert not invitacion.user.is_active
    assert not invitacion.user.has_usable_password()


def test_el_token_no_se_puede_usar_dos_veces(invitacion):
    accept_invitation(invitacion.token, CLAVE_VALIDA)
    with pytest.raises(InvitationAlreadyUsedError):
        accept_invitation(invitacion.token, "Otra-Clave-Distinta-9")
    assert authenticate(username="vecino@example.com", password=CLAVE_VALIDA)


def test_un_token_reemplazado_por_reinvitacion_ya_no_sirve(admin):
    primera = invite_user("vecino@example.com", invited_by=admin)
    segunda = invite_user("vecino@example.com", invited_by=admin)
    with pytest.raises(InvitationExpiredError):
        accept_invitation(primera.token, CLAVE_VALIDA)
    accept_invitation(segunda.token, CLAVE_VALIDA)


@pytest.mark.parametrize(
    "clave",
    ["Ab1x", "12345678901", "password", "vecino@example.com"],
    ids=["corta", "solo-numeros", "comun", "igual-al-correo"],
)
def test_clave_debil_se_rechaza_y_la_invitacion_sigue_valida(invitacion, clave):
    with pytest.raises(ValidationError):
        accept_invitation(invitacion.token, clave)
    invitation = Invitation.objects.get(pk=invitacion.invitation.pk)
    assert invitation.is_usable
    invitacion.user.refresh_from_db()
    assert not invitacion.user.is_active


def test_todos_los_errores_de_invitacion_comparten_base():
    for error in (
        InvitationNotFoundError,
        InvitationExpiredError,
        InvitationAlreadyUsedError,
    ):
        assert issubclass(error, InvitationError)


def test_aceptar_no_cambia_el_correo(invitacion):
    user = accept_invitation(invitacion.token, CLAVE_VALIDA)
    assert user.email == "vecino@example.com"
