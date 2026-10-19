"""Reglas de propiedades: ocupaciones, titular principal, roles y vínculo persona-usuario."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.services import invite_user
from apps.core.audit import log_event

from .models import Person, RoleAssignment, UnitOccupancy


class PropertiesError(Exception):
    """Base de los errores de reglas de propiedades."""


class InvalidOccupancyError(PropertiesError):
    """Los datos de la ocupación no cumplen las reglas."""


class OccupancyConflictError(PropertiesError):
    """Ya existe una ocupación vigente igual."""


class RoleAlreadyAssignedError(PropertiesError):
    """El usuario ya tiene ese rol en esa torre."""


class PersonAlreadyLinkedError(PropertiesError):
    """La persona ya tiene un usuario."""


class MissingEmailError(PropertiesError):
    """No hay correo para enviar la invitación."""


class UserLinkedToOtherPersonError(PropertiesError):
    """El correo pertenece a un usuario que ya está enlazado con otra persona."""


def _demote_current_primary(unit):
    current = list(
        UnitOccupancy.objects.select_for_update().filter(
            unit=unit, is_primary=True, end_date__isnull=True
        )
    )
    for occupancy in current:
        occupancy.is_primary = False
        occupancy.save(update_fields=["is_primary", "updated_at"])
    return current


def add_occupancy(
    unit,
    person,
    relation,
    start_date=None,
    is_primary=False,
    end_date=None,
    actor=None,
    ip=None,
):
    """Crea una ocupación. Un nuevo titular principal reemplaza al anterior."""
    start_date = start_date or timezone.localdate()
    if is_primary and relation != UnitOccupancy.Relation.OWNER:
        raise InvalidOccupancyError("Solo un propietario puede ser titular principal.")
    if end_date is not None and end_date < start_date:
        raise InvalidOccupancyError("La fecha final no puede ser anterior al inicio.")

    try:
        with transaction.atomic():
            replaced = []
            if end_date is None:
                if UnitOccupancy.objects.filter(
                    unit=unit, person=person, relation=relation, end_date__isnull=True
                ).exists():
                    raise OccupancyConflictError(
                        "La persona ya tiene esa relación vigente."
                    )
                if is_primary:
                    replaced = _demote_current_primary(unit)
            occupancy = UnitOccupancy.objects.create(
                unit=unit,
                person=person,
                relation=relation,
                is_primary=is_primary,
                start_date=start_date,
                end_date=end_date,
            )
            log_event(
                "OCCUPANCY_CREATED",
                "UnitOccupancy",
                occupancy.pk,
                actor=actor,
                changes={
                    "unit": unit.code,
                    "person_id": str(person.pk),
                    "relation": relation,
                    "is_primary": is_primary,
                },
                ip=ip,
            )
            if replaced:
                log_event(
                    "PRIMARY_HOLDER_CHANGED",
                    "Unit",
                    unit.pk,
                    actor=actor,
                    changes={
                        "unit": unit.code,
                        "from_person_id": str(replaced[0].person_id),
                        "to_person_id": str(person.pk),
                    },
                    ip=ip,
                )
    except IntegrityError as exc:  # carrera entre dos solicitudes simultáneas
        raise OccupancyConflictError("Conflicto con otra ocupación vigente.") from exc
    return occupancy


def make_primary(occupancy, actor=None, ip=None):
    """Designa a un propietario vigente como titular principal de la unidad."""
    if not occupancy.is_active:
        raise InvalidOccupancyError("La ocupación ya terminó.")
    if occupancy.relation != UnitOccupancy.Relation.OWNER:
        raise InvalidOccupancyError("Solo un propietario puede ser titular principal.")
    if occupancy.is_primary:
        return occupancy

    with transaction.atomic():
        previous = _demote_current_primary(occupancy.unit)
        occupancy.is_primary = True
        occupancy.save(update_fields=["is_primary", "updated_at"])
        log_event(
            "PRIMARY_HOLDER_CHANGED",
            "Unit",
            occupancy.unit_id,
            actor=actor,
            changes={
                "unit": occupancy.unit.code,
                "from_person_id": str(previous[0].person_id) if previous else "",
                "to_person_id": str(occupancy.person_id),
            },
            ip=ip,
        )
    return occupancy


def close_occupancy(occupancy, end_date=None, actor=None, ip=None):
    """Termina una ocupación vigente (venta, fin de alquiler)."""
    end_date = end_date or timezone.localdate()
    if not occupancy.is_active:
        raise InvalidOccupancyError("La ocupación ya estaba cerrada.")
    if end_date < occupancy.start_date:
        raise InvalidOccupancyError("La fecha final no puede ser anterior al inicio.")
    if end_date > timezone.localdate():
        raise InvalidOccupancyError("La fecha final no puede ser futura.")

    with transaction.atomic():
        occupancy.end_date = end_date
        occupancy.save(update_fields=["end_date", "updated_at"])
        log_event(
            "OCCUPANCY_CLOSED",
            "UnitOccupancy",
            occupancy.pk,
            actor=actor,
            changes={
                "unit": occupancy.unit.code,
                "person_id": str(occupancy.person_id),
                "end_date": end_date.isoformat(),
            },
            ip=ip,
        )
    return occupancy


def assign_role(user, role, tower, actor=None, ip=None):
    try:
        with transaction.atomic():
            assignment = RoleAssignment.objects.create(
                user=user, role=role, tower=tower
            )
            log_event(
                "ROLE_ASSIGNED",
                "RoleAssignment",
                assignment.pk,
                actor=actor,
                changes={"user": user.email, "role": role, "tower": tower.name},
                ip=ip,
            )
    except IntegrityError as exc:
        raise RoleAlreadyAssignedError from exc
    return assignment


def revoke_role(assignment, actor=None, ip=None):
    with transaction.atomic():
        log_event(
            "ROLE_REVOKED",
            "RoleAssignment",
            assignment.pk,
            actor=actor,
            changes={
                "user": assignment.user.email,
                "role": assignment.role,
                "tower": assignment.tower.name,
            },
            ip=ip,
        )
        assignment.delete()


def invite_person(person, email=None, invited_by=None, ip=None):
    """Invita por correo a una persona y la enlaza con la cuenta creada."""
    if person.user_id:
        raise PersonAlreadyLinkedError
    email = (email or person.email or "").strip().lower()
    if not email:
        raise MissingEmailError

    with transaction.atomic():
        result = invite_user(email, invited_by=invited_by, ip=ip)
        if Person.objects.filter(user=result.user).exclude(pk=person.pk).exists():
            raise UserLinkedToOtherPersonError
        person.user = result.user
        update_fields = ["user", "updated_at"]
        if not person.email:
            person.email = email
            update_fields.append("email")
        person.save(update_fields=update_fields)
        log_event(
            "PERSON_INVITED",
            "Person",
            person.pk,
            actor=invited_by,
            changes={"user_id": str(result.user.pk)},
            ip=ip,
        )
    return result
