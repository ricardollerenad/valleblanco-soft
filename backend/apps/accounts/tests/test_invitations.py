"""T2: emisión de invitaciones (servicio `invite_user`)."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import Invitation
from apps.accounts.services import (
    INVITATION_TTL,
    UserAlreadyActiveError,
    hash_token,
    invite_user,
)

pytestmark = pytest.mark.django_db

User = get_user_model()
STRONG = "Cielo-Azul-9347-Zq"
EMAIL = "nuevo@valleblanco.pe"


def test_invitar_crea_un_usuario_inactivo_sin_clave():
    result = invite_user(EMAIL)

    assert result.user.email == EMAIL
    assert not result.user.is_active
    assert not result.user.has_usable_password()


def test_invitacion_recien_creada_es_utilizable():
    result = invite_user(EMAIL)

    assert result.invitation.is_usable
    assert not result.invitation.is_expired
    assert result.invitation.accepted_at is None


def test_invitacion_vencida_no_es_utilizable():
    invitation = invite_user(EMAIL).invitation
    Invitation.objects.filter(pk=invitation.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    invitation.refresh_from_db()

    assert invitation.is_expired
    assert not invitation.is_usable


def test_invitacion_aceptada_no_es_utilizable():
    invitation = invite_user(EMAIL).invitation
    invitation.accepted_at = timezone.now()
    invitation.save()

    assert not invitation.is_usable


def test_la_invitacion_vence_a_las_72_horas():
    invitation = invite_user(EMAIL).invitation

    assert INVITATION_TTL == timedelta(hours=72)
    delta = invitation.expires_at - invitation.created_at
    assert abs(delta - timedelta(hours=72)) < timedelta(minutes=1)


def test_el_token_es_largo_y_distinto_en_cada_invitacion():
    first = invite_user("uno@valleblanco.pe")
    second = invite_user("dos@valleblanco.pe")

    assert len(first.token) >= 40
    assert first.token != second.token


def test_solo_se_guarda_el_hash_del_token():
    result = invite_user(EMAIL)

    assert result.invitation.token_hash == hash_token(result.token)
    assert len(result.invitation.token_hash) == 64
    assert result.token not in result.invitation.token_hash
    assert not Invitation.objects.filter(token_hash=result.token).exists()


def test_la_invitacion_registra_quien_invito():
    admin = User.objects.create_superuser("admin@valleblanco.pe", STRONG)

    result = invite_user(EMAIL, invited_by=admin)

    assert result.invitation.invited_by == admin


def test_reinvitar_invalida_la_anterior_y_reutiliza_el_usuario():
    first = invite_user(EMAIL)
    second = invite_user(EMAIL)

    first.invitation.refresh_from_db()
    assert second.user.pk == first.user.pk
    assert User.objects.filter(email=EMAIL).count() == 1
    assert not first.invitation.is_usable
    assert second.invitation.is_usable
    assert first.token != second.token


def test_no_se_invita_a_un_usuario_que_ya_tiene_cuenta():
    User.objects.create_user(EMAIL, STRONG)

    with pytest.raises(UserAlreadyActiveError):
        invite_user(EMAIL)


def test_no_se_invita_a_un_usuario_desactivado_con_clave():
    User.objects.create_user(EMAIL, STRONG, is_active=False)

    with pytest.raises(UserAlreadyActiveError):
        invite_user(EMAIL)


def test_correo_invalido_se_rechaza():
    with pytest.raises(ValidationError):
        invite_user("esto-no-es-un-correo")

    assert not User.objects.filter(email="esto-no-es-un-correo").exists()
