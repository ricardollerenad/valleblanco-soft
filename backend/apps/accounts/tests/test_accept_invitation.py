"""T3: aceptar una invitación (servicio `accept_invitation`)."""

from datetime import timedelta

import pytest
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

pytestmark = pytest.mark.django_db

EMAIL = "nuevo@valleblanco.pe"
STRONG = "Cielo-Azul-9347-Zq"


def test_aceptar_activa_la_cuenta_y_fija_la_clave():
    result = invite_user(EMAIL)

    user = accept_invitation(result.token, STRONG)

    user.refresh_from_db()
    assert user.pk == result.user.pk
    assert user.is_active
    assert user.check_password(STRONG)


def test_aceptar_marca_la_invitacion_como_usada():
    result = invite_user(EMAIL)

    accept_invitation(result.token, STRONG)

    result.invitation.refresh_from_db()
    assert result.invitation.accepted_at is not None
    assert not result.invitation.is_usable


def test_aceptar_no_cambia_el_correo():
    result = invite_user(EMAIL)

    user = accept_invitation(result.token, STRONG)

    assert user.email == EMAIL


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("password", id="comun"),
        pytest.param("Ab1-xy", id="corta"),
        pytest.param(EMAIL, id="igual-al-correo"),
        pytest.param("83947261058", id="solo-numeros"),
    ],
)
def test_clave_debil_se_rechaza_y_la_invitacion_sigue_valida(password):
    result = invite_user(EMAIL)

    with pytest.raises(ValidationError):
        accept_invitation(result.token, password)

    result.invitation.refresh_from_db()
    result.user.refresh_from_db()
    assert result.invitation.is_usable
    assert not result.user.is_active
    assert not result.user.has_usable_password()


def test_el_token_no_se_puede_usar_dos_veces():
    result = invite_user(EMAIL)
    accept_invitation(result.token, STRONG)

    with pytest.raises(InvitationAlreadyUsedError):
        accept_invitation(result.token, STRONG)


def test_token_inexistente_se_rechaza():
    with pytest.raises(InvitationNotFoundError):
        accept_invitation("token-que-no-existe", STRONG)


@pytest.mark.parametrize("token", ["", "   ", None])
def test_token_vacio_se_rechaza(token):
    with pytest.raises(InvitationNotFoundError):
        accept_invitation(token, STRONG)


def test_token_vencido_se_rechaza_y_no_activa_nada():
    result = invite_user(EMAIL)
    Invitation.objects.filter(pk=result.invitation.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    with pytest.raises(InvitationExpiredError):
        accept_invitation(result.token, STRONG)

    result.user.refresh_from_db()
    assert not result.user.is_active
    assert not result.user.has_usable_password()


def test_un_token_reemplazado_por_reinvitacion_ya_no_sirve():
    first = invite_user(EMAIL)
    second = invite_user(EMAIL)

    with pytest.raises(InvitationExpiredError):
        accept_invitation(first.token, STRONG)

    user = accept_invitation(second.token, STRONG)
    assert user.is_active


def test_todos_los_errores_de_invitacion_comparten_base():
    for error in (
        InvitationNotFoundError,
        InvitationExpiredError,
        InvitationAlreadyUsedError,
    ):
        assert issubclass(error, InvitationError)
