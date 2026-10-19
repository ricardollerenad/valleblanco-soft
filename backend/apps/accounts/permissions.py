"""Permisos de cuentas. El rol ADMIN viene de `core.profile` (superusuario o por torre)."""

from rest_framework.permissions import BasePermission

from apps.core.profile import get_roles


class IsAdmin(BasePermission):
    """Administrador: superusuario o con el rol ADMIN en alguna torre."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and "ADMIN" in get_roles(user))
