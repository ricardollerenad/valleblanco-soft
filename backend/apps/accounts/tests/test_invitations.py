"""S1 · T2 — Invitaciones: el enlace lo genera la administración (sin correo el día 1)."""

import hashlib
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import Invitation
from apps.accounts.services import UserAlreadyActiveError, invite_user

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    return User.objects.create_superuser("admin@example.com", "clave-segura-123")


def test_invitar_crea_un_usuario_inactivo_sin_clave(admin):
    result = invite_user("Vecino@Example.com", invited_by=admin)
    assert result.user.email == "vecino@example.com"
    assert not result.user.is_active
    assert not result.user.has_usable_password()


def test_el_token_es_largo_y_distinto_en_cada_invitacion(admin):
    primero = invite_user("a@example.com", invited_by=admin)
    segundo = invite_user("b@example.com", invited_by=admin)
    assert len(primero.token) >= 32
    assert primero.token != segundo.token


def test_solo_se_guarda_el_hash_del_token(admin):
    result = invite_user("a@example.com", invited_by=admin)
    esperado = hashlib.sha256(result.token.encode()).hexdigest()
    assert result.invitation.token_hash == esperado
    assert not Invitation.objects.filter(token_hash=result.token).exists()


def test_la_invitacion_vence_a_las_72_horas(admin):
    result = invite_user("a@example.com", invited_by=admin)
    esperado = timezone.now() + timedelta(hours=72)
    assert abs(result.invitation.expires_at - esperado) < timedelta(seconds=5)


def test_la_invitacion_registra_quien_invito(admin):
    result = invite_user("a@example.com", invited_by=admin)
    assert result.invitation.invited_by == admin
    assert result.invitation.user == result.user
    assert result.invitation.accepted_at is None


def test_invitacion_recien_creada_es_utilizable(admin):
    result = invite_user("a@example.com", invited_by=admin)
    assert not result.invitation.is_expired
    assert result.invitation.is_usable


def test_invitacion_vencida_no_es_utilizable(admin):
    result = invite_user("a@example.com", invited_by=admin)
    Invitation.objects.filter(pk=result.invitation.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    invitation = Invitation.objects.get(pk=result.invitation.pk)
    assert invitation.is_expired
    assert not invitation.is_usable


def test_invitacion_aceptada_no_es_utilizable(admin):
    result = invite_user("a@example.com", invited_by=admin)
    Invitation.objects.filter(pk=result.invitation.pk).update(
        accepted_at=timezone.now()
    )
    invitation = Invitation.objects.get(pk=result.invitation.pk)
    assert not invitation.is_usable


def test_reinvitar_invalida_la_anterior_y_reutiliza_el_usuario(admin):
    primera = invite_user("a@example.com", invited_by=admin)
    segunda = invite_user("A@example.com", invited_by=admin)
    assert segunda.user.pk == primera.user.pk
    assert User.objects.filter(email="a@example.com").count() == 1
    anterior = Invitation.objects.get(pk=primera.invitation.pk)
    assert not anterior.is_usable
    assert segunda.invitation.is_usable


def test_no_se_invita_a_un_usuario_que_ya_tiene_cuenta(admin):
    User.objects.create_user("activo@example.com", "clave-segura-123")
    with pytest.raises(UserAlreadyActiveError):
        invite_user("activo@example.com", invited_by=admin)


def test_no_se_invita_a_un_usuario_desactivado_con_clave(admin):
    user = User.objects.create_user("baja@example.com", "clave-segura-123")
    user.is_active = False
    user.save()
    with pytest.raises(UserAlreadyActiveError):
        invite_user("baja@example.com", invited_by=admin)


def test_correo_invalido_se_rechaza(admin):
    with pytest.raises(ValidationError):
        invite_user("esto-no-es-un-correo", invited_by=admin)
    assert not User.objects.filter(email="esto-no-es-un-correo").exists()
