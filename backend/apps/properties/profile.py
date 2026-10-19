"""Aporta roles por torre, roles derivados de ocupaciones y unidades al perfil."""

from .models import RoleAssignment, UnitOccupancy


def profile_provider(user):
    roles = set()
    for role in RoleAssignment.objects.filter(user=user).values_list("role", flat=True):
        roles.add(role)
        if role == RoleAssignment.Role.TREASURER:
            roles.add(RoleAssignment.Role.BOARD)  # el tesorero es parte de la junta

    units = []
    occupancies = (
        UnitOccupancy.objects.filter(person__user=user, end_date__isnull=True)
        .select_related("unit")
        .order_by("unit__floor", "unit__number")
    )
    for occupancy in occupancies:
        roles.add(occupancy.relation)  # propietario/inquilino se derivan, no se guardan
        units.append(
            {
                "id": str(occupancy.unit_id),
                "code": occupancy.unit.code,
                "relation": occupancy.relation,
                "is_primary": occupancy.is_primary,
            }
        )
    return {"roles": roles, "units": units}
