"""Roles y unidades del usuario, reunidos de las apps que los conocen.

`accounts` no puede importar `properties` (la dependencia es `accounts ◄ properties`),
así que cada app registra aquí un proveedor y `accounts` solo consulta este módulo.
"""

ROLE_ORDER = ("ADMIN", "BOARD", "TREASURER", "OWNER", "TENANT")

_providers = []


def register_profile_provider(provider):
    """Registra una función `provider(user) -> {"roles": [...], "units": [...]}`."""
    if provider not in _providers:
        _providers.append(provider)
    return provider


def collect_profile(user):
    """Devuelve `(roles ordenados, unidades)` del usuario."""
    roles = set()
    units = []
    if user.is_superuser:
        roles.add("ADMIN")
    for provider in _providers:
        data = provider(user)
        roles.update(data.get("roles", ()))
        units.extend(data.get("units", ()))
    known = [role for role in ROLE_ORDER if role in roles]
    extra = sorted(roles.difference(ROLE_ORDER))
    return known + extra, units


def get_roles(user):
    return set(collect_profile(user)[0])
