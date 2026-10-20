"""Permisos de finanzas. El administrador usa `accounts.permissions.IsAdmin`."""

from rest_framework.permissions import BasePermission

from apps.core.profile import get_roles
from apps.properties.permissions import STAFF_ROLES

# Quienes revisan pagos: la junta sin tesorería NO aprueba ni rechaza.
REVIEW_ROLES = frozenset({"ADMIN", "TREASURER"})


class IsStaff(BasePermission):
    """Administración, junta directiva o tesorería (el tesorero implica junta)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and get_roles(user) & STAFF_ROLES)


class IsTreasurerOrAdmin(BasePermission):
    """Administración o tesorería: pueden aprobar y rechazar pagos."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and get_roles(user) & REVIEW_ROLES)
