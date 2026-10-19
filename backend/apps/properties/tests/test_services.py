"""S2: reglas de ocupaciones, titular principal, roles y vínculo persona-usuario."""

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import Invitation
from apps.accounts.services import UserAlreadyActiveError
from apps.core.models import AuditLog
from apps.properties.models import RoleAssignment, UnitOccupancy
from apps.properties.services import (
    InvalidOccupancyError,
    MissingEmailError,
    OccupancyConflictError,
    PersonAlreadyLinkedError,
    RoleAlreadyAssignedError,
    UserLinkedToOtherPersonError,
    add_occupancy,
    assign_role,
    close_occupancy,
    invite_person,
    make_primary,
    revoke_role,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


def actions():
    return list(
        AuditLog.objects.order_by("created_at").values_list("action", flat=True)
    )


# --- add_occupancy -----------------------------------------------------------


def test_crear_ocupacion_registra_auditoria(unit, make_person, admin):
    occupancy = add_occupancy(
        unit, make_person(), "OWNER", is_primary=True, actor=admin, ip="10.0.0.1"
    )

    entry = AuditLog.objects.get(action="OCCUPANCY_CREATED")
    assert entry.entity_id == str(occupancy.pk)
    assert entry.actor_id == str(admin.pk)
    assert entry.ip_address == "10.0.0.1"
    assert entry.changes["unit"] == "101"
    assert entry.changes["is_primary"] is True


def test_nuevo_titular_principal_reemplaza_al_anterior(unit, make_person):
    first = add_occupancy(unit, make_person("Uno"), "OWNER", is_primary=True)
    second = add_occupancy(unit, make_person("Dos"), "OWNER", is_primary=True)

    first.refresh_from_db()
    assert first.is_primary is False
    assert first.is_active is True  # sigue siendo copropietario
    assert second.is_primary is True
    assert actions().count("PRIMARY_HOLDER_CHANGED") == 1


def test_un_inquilino_no_puede_ser_titular_principal(unit, make_person):
    with pytest.raises(InvalidOccupancyError):
        add_occupancy(unit, make_person(), "TENANT", is_primary=True)
    assert UnitOccupancy.objects.count() == 0


def test_la_misma_relacion_vigente_no_se_repite(unit, make_person):
    person = make_person()
    add_occupancy(unit, person, "OWNER")
    with pytest.raises(OccupancyConflictError):
        add_occupancy(unit, person, "OWNER")


def test_fecha_final_anterior_al_inicio_es_invalida(unit, make_person):
    with pytest.raises(InvalidOccupancyError):
        add_occupancy(
            unit,
            make_person(),
            "OWNER",
            start_date=date(2025, 5, 1),
            end_date=date(2025, 4, 1),
        )


def test_se_puede_cargar_una_ocupacion_historica_ya_cerrada(unit, make_person):
    occupancy = add_occupancy(
        unit,
        make_person(),
        "OWNER",
        is_primary=True,
        start_date=date(2020, 1, 1),
        end_date=date(2022, 1, 1),
    )
    assert occupancy.is_active is False
    # un histórico no desplaza al titular vigente
    current = add_occupancy(unit, make_person("Vigente"), "OWNER", is_primary=True)
    assert current.is_primary is True


# --- make_primary ------------------------------------------------------------


def test_designar_titular_principal_cambia_al_anterior(unit, make_person):
    first = add_occupancy(unit, make_person("Uno"), "OWNER", is_primary=True)
    second = add_occupancy(unit, make_person("Dos"), "OWNER")

    make_primary(second)

    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.is_primary, second.is_primary) == (False, True)
    assert "PRIMARY_HOLDER_CHANGED" in actions()


def test_designar_al_que_ya_es_titular_no_hace_nada(unit, make_person):
    holder = add_occupancy(unit, make_person(), "OWNER", is_primary=True)
    before = AuditLog.objects.count()

    make_primary(holder)

    assert AuditLog.objects.count() == before


def test_un_inquilino_no_puede_ser_designado_titular(unit, make_person):
    tenant = add_occupancy(unit, make_person(), "TENANT")
    with pytest.raises(InvalidOccupancyError):
        make_primary(tenant)


def test_una_ocupacion_cerrada_no_puede_ser_titular(unit, make_person):
    old = add_occupancy(unit, make_person(), "OWNER", start_date=date(2020, 1, 1))
    close_occupancy(old, date(2021, 1, 1))
    with pytest.raises(InvalidOccupancyError):
        make_primary(old)


# --- close_occupancy ---------------------------------------------------------


def test_cerrar_ocupacion_fija_la_fecha_y_audita(unit, make_person, admin):
    occupancy = add_occupancy(unit, make_person(), "OWNER", start_date=date(2024, 1, 1))

    close_occupancy(occupancy, date(2025, 6, 30), actor=admin)

    occupancy.refresh_from_db()
    assert occupancy.end_date == date(2025, 6, 30)
    assert occupancy.is_active is False
    assert (
        AuditLog.objects.get(action="OCCUPANCY_CLOSED").changes["end_date"]
        == "2025-06-30"
    )


def test_cerrar_sin_fecha_usa_hoy(unit, make_person):
    occupancy = add_occupancy(unit, make_person(), "OWNER", start_date=date(2024, 1, 1))
    close_occupancy(occupancy)
    occupancy.refresh_from_db()
    assert occupancy.end_date == timezone.localdate()


def test_cerrar_con_fecha_futura_es_invalido(unit, make_person):
    occupancy = add_occupancy(unit, make_person(), "OWNER", start_date=date(2024, 1, 1))
    with pytest.raises(InvalidOccupancyError):
        close_occupancy(occupancy, timezone.localdate() + timedelta(days=1))


def test_cerrar_antes_del_inicio_es_invalido(unit, make_person):
    occupancy = add_occupancy(unit, make_person(), "OWNER", start_date=date(2024, 1, 1))
    with pytest.raises(InvalidOccupancyError):
        close_occupancy(occupancy, date(2023, 12, 31))


def test_cerrar_dos_veces_es_invalido(unit, make_person):
    occupancy = add_occupancy(unit, make_person(), "OWNER", start_date=date(2024, 1, 1))
    close_occupancy(occupancy, date(2024, 6, 1))
    with pytest.raises(InvalidOccupancyError):
        close_occupancy(occupancy, date(2024, 7, 1))


# --- Roles -------------------------------------------------------------------


def test_asignar_rol_audita_y_no_se_repite(tower, make_user, admin):
    user = make_user("junta@valleblanco.pe")

    assignment = assign_role(user, "BOARD", tower, actor=admin, ip="10.0.0.2")

    entry = AuditLog.objects.get(action="ROLE_ASSIGNED")
    assert entry.entity_id == str(assignment.pk)
    assert entry.changes == {
        "user": "junta@valleblanco.pe",
        "role": "BOARD",
        "tower": "Torre 2",
    }
    assert entry.ip_address == "10.0.0.2"
    with pytest.raises(RoleAlreadyAssignedError):
        assign_role(user, "BOARD", tower)


def test_revocar_rol_lo_elimina_y_audita(tower, make_user, admin):
    assignment = assign_role(make_user("junta@valleblanco.pe"), "BOARD", tower)

    revoke_role(assignment, actor=admin)

    assert RoleAssignment.objects.count() == 0
    assert AuditLog.objects.get(action="ROLE_REVOKED").changes["role"] == "BOARD"


# --- invite_person -----------------------------------------------------------


def test_invitar_persona_crea_usuario_y_la_enlaza(make_person, admin):
    person = make_person("Ana", email="ana@valleblanco.pe")

    result = invite_person(person, invited_by=admin, ip="10.0.0.3")

    person.refresh_from_db()
    assert person.user == result.user
    assert result.user.is_active is False
    assert "PERSON_INVITED" in actions()
    assert "INVITATION_CREATED" in actions()


def test_el_correo_indicado_se_guarda_si_la_persona_no_tenia(make_person):
    person = make_person("Ana")

    invite_person(person, email="Ana@ValleBlanco.pe")

    person.refresh_from_db()
    assert person.email == "ana@valleblanco.pe"


def test_el_correo_indicado_no_pisa_el_que_ya_tenia(make_person):
    person = make_person("Ana", email="ana@valleblanco.pe")

    invite_person(person, email="otro@valleblanco.pe")

    person.refresh_from_db()
    assert person.email == "ana@valleblanco.pe"
    assert person.user.email == "otro@valleblanco.pe"


def test_sin_correo_no_se_puede_invitar(make_person):
    with pytest.raises(MissingEmailError):
        invite_person(make_person("Ana"))


def test_una_persona_ya_enlazada_no_se_invita_de_nuevo(make_person, make_user):
    person = make_person("Ana", user=make_user("ana@valleblanco.pe"))
    with pytest.raises(PersonAlreadyLinkedError):
        invite_person(person, email="otro@valleblanco.pe")


def test_un_usuario_activo_no_se_invita(make_person, make_user):
    make_user("activo@valleblanco.pe")
    with pytest.raises(UserAlreadyActiveError):
        invite_person(make_person("Ana"), email="activo@valleblanco.pe")


def test_si_el_usuario_pertenece_a_otra_persona_no_queda_rastro(make_person):
    first = make_person("Uno")
    invite_person(first, email="pendiente@valleblanco.pe")
    invitations_before = Invitation.objects.count()
    audit_before = AuditLog.objects.count()

    with pytest.raises(UserLinkedToOtherPersonError):
        invite_person(make_person("Dos"), email="pendiente@valleblanco.pe")

    assert Invitation.objects.count() == invitations_before
    assert AuditLog.objects.count() == audit_before
    assert User.objects.filter(email="pendiente@valleblanco.pe").count() == 1
