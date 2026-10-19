"""Permisos de finanzas. El administrador usa `accounts.permissions.IsAdmin`."""

from rest_framework.permissions import BasePermission

from apps.core.profile import get_roles
from apps.properties.permissions import STAFF_ROLES


class IsStaff(BasePermission):
    """Administración, junta directiva o tesorería (el tesorero implica junta)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and get_roles(user) & STAFF_ROLES)
