"""Servicios de cuentas. La lógica de negocio vive aquí, no en las vistas."""

import hashlib
import secrets
from datetime import timedelta
from typing import NamedTuple

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from .models import Invitation

INVITATION_TTL = timedelta(hours=72)


class UserAlreadyActiveError(Exception):
    """El correo ya tiene una cuenta que no está pendiente de invitación."""


class InvitationError(Exception):
    """Base de los errores al aceptar una invitación."""


class InvitationNotFoundError(InvitationError):
    """El token no corresponde a ninguna invitación."""


class InvitationExpiredError(InvitationError):
    """La invitación venció o fue reemplazada por una más nueva."""


class InvitationAlreadyUsedError(InvitationError):
    """La invitación ya se aceptó."""


class InvitationResult(NamedTuple):
    user: object
    invitation: Invitation
    token: str  # token en claro: se muestra una sola vez y no se guarda


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def invite_user(email, invited_by=None):
    """Crea (o reutiliza) el usuario pendiente y emite una invitación nueva."""
    User = get_user_model()
    email = (email or "").strip().lower()
    validate_email(email)

    with transaction.atomic():
        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create_user(email, password=None, is_active=False)
        elif user.is_active or user.has_usable_password():
            raise UserAlreadyActiveError(email)

        now = timezone.now()
        Invitation.objects.filter(
            user=user, accepted_at__isnull=True, expires_at__gt=now
        ).update(expires_at=now)

        token = secrets.token_urlsafe(32)
        invitation = Invitation.objects.create(
            user=user,
            token_hash=hash_token(token),
            expires_at=now + INVITATION_TTL,
            invited_by=invited_by,
        )
    return InvitationResult(user=user, invitation=invitation, token=token)


def accept_invitation(token, password):
    """Activa la cuenta invitada y fija su contraseña. El token sirve una sola vez."""
    token_hash = hash_token((token or "").strip())

    with transaction.atomic():
        invitation = (
            Invitation.objects.select_for_update(of=("self",))
            .select_related("user")
            .filter(token_hash=token_hash)
            .first()
        )
        if invitation is None:
            raise InvitationNotFoundError
        if invitation.accepted_at is not None:
            raise InvitationAlreadyUsedError
        if invitation.is_expired:
            raise InvitationExpiredError

        user = invitation.user
        validate_password(password, user)

        user.set_password(password)
        user.is_active = True
        user.save()

        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at", "updated_at"])
    return user
