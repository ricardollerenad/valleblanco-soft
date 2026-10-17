"""Servicios de cuentas. La lógica de negocio vive aquí, no en las vistas."""

import hashlib
import secrets
from datetime import timedelta
from typing import NamedTuple

from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from .models import Invitation

INVITATION_TTL = timedelta(hours=72)


class UserAlreadyActiveError(Exception):
    """El correo ya pertenece a una cuenta que existe y no está pendiente de invitación."""


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
