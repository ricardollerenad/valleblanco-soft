"""S2: propietarios, inquilinos y titular principal por unidad."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.core.models import AuditLog
from apps.properties.models import UnitOccupancy
from apps.properties.tests.conftest import client_for

pytestmark = pytest.mark.django_db

MISSING = "00000000-0000-4000-8000-000000000000"


def list_url(unit):
    return f"/api/v1/units/{unit.pk}/occupancies/"


def detail(occupancy):
    return f"/api/v1/occupancies/{occupancy.pk}/"


# --- Permisos ----------------------------------------------------------------


def test_el_propietario_de_la_unidad_no_gestiona_ocupaciones(owner, unit, make_person):
    client = client_for(owner)
    person = make_person("Otra")
    assert client.get(list_url(unit)).status_code == 403
    assert (
        client.post(
            list_url(unit),
            {"person": str(person.pk), "relation": "TENANT"},
            format="json",
        ).status_code
        == 403
    )


def test_la_junta_no_gestiona_ocupaciones(unit, make_user, give_role):
    user = make_user("junta@valleblanco.pe")
    give_role(user, "BOARD")
    assert client_for(user).get(list_url(unit)).status_code == 403


def test_unidad_inexistente_devuelve_404(admin_client):
    assert admin_client.get(f"/api/v1/units/{MISSING}/occupancies/").status_code == 404


# --- Listar ------------------------------------------------------------------


def test_lista_las_ocupaciones_de_la_unidad_y_filtra_vigentes(
    admin_client, unit, unit2, make_person, make_occupancy
):
    make_occupancy(unit, make_person("Vigente"), "OWNER", is_primary=True)
    make_occupancy(
        unit,
        make_person("Antiguo"),
        "OWNER",
        start_date=date(2020, 1, 1),
        end_date=date(2023, 1, 1),
    )
    make_occupancy(unit2, make_person("Otra unidad"), "OWNER")

    everything = admin_client.get(list_url(unit)).json()
    active = admin_client.get(list_url(unit), {"active": "true"}).json()
    ended = admin_client.get(list_url(unit), {"active": "false"}).json()

    assert everything["count"] == 2
    assert [o["person_name"] for o in active["results"]] == ["Vigente"]
    assert [o["person_name"] for o in ended["results"]] == ["Antiguo"]
    first = active["results"][0]
    assert first["unit_code"] == "101"
    assert first["is_primary"] is True
    assert first["is_active"] is True


# --- Crear -------------------------------------------------------------------


def test_admin_agrega_un_propietario_titular(admin_client, unit, make_person):
    person = make_person("Ana")

    response = admin_client.post(
        list_url(unit),
        {
            "person": str(person.pk),
            "relation": "OWNER",
            "is_primary": True,
            "start_date": "2024-02-01",
        },
        format="json",
    )

    assert response.status_code == 201
    data = response.json()
    assert data["is_primary"] is True
    assert data["start_date"] == "2024-02-01"
    assert data["end_date"] is None
    assert AuditLog.objects.filter(action="OCCUPANCY_CREATED").count() == 1


def test_sin_fecha_de_inicio_usa_hoy(admin_client, unit, make_person):
    response = admin_client.post(
        list_url(unit),
        {"person": str(make_person().pk), "relation": "TENANT"},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["start_date"] == timezone.localdate().isoformat()


def test_un_nuevo_titular_reemplaza_al_anterior(
    admin_client, unit, make_person, make_occupancy
):
    old = make_occupancy(unit, make_person("Antiguo"), "OWNER", is_primary=True)
    new = make_person("Nuevo")

    response = admin_client.post(
        list_url(unit),
        {"person": str(new.pk), "relation": "OWNER", "is_primary": True},
        format="json",
    )

    assert response.status_code == 201
    old.refresh_from_db()
    assert old.is_primary is False
    assert (
        UnitOccupancy.objects.filter(
            unit=unit, is_primary=True, end_date__isnull=True
        ).count()
        == 1
    )
    assert AuditLog.objects.filter(action="PRIMARY_HOLDER_CHANGED").count() == 1


def test_un_inquilino_titular_devuelve_400(admin_client, unit, make_person):
    response = admin_client.post(
        list_url(unit),
        {"person": str(make_person().pk), "relation": "TENANT", "is_primary": True},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert UnitOccupancy.objects.count() == 0


def test_repetir_la_misma_relacion_vigente_devuelve_409(
    admin_client, unit, make_person
):
    person = make_person()
    body = {"person": str(person.pk), "relation": "OWNER"}
    admin_client.post(list_url(unit), body, format="json")

    response = admin_client.post(list_url(unit), body, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "occupancy_conflict"


def test_persona_inexistente_devuelve_400(admin_client, unit):
    response = admin_client.post(
        list_url(unit), {"person": MISSING, "relation": "OWNER"}, format="json"
    )
    assert response.status_code == 400
    assert "person" in response.json()["errors"]


def test_relacion_invalida_devuelve_400(admin_client, unit, make_person):
    response = admin_client.post(
        list_url(unit),
        {"person": str(make_person().pk), "relation": "VISITA"},
        format="json",
    )
    assert response.status_code == 400
    assert "relation" in response.json()["errors"]


def test_fecha_final_anterior_al_inicio_devuelve_400(admin_client, unit, make_person):
    response = admin_client.post(
        list_url(unit),
        {
            "person": str(make_person().pk),
            "relation": "OWNER",
            "start_date": "2025-05-01",
            "end_date": "2025-04-01",
        },
        format="json",
    )
    assert response.status_code == 400


# --- Cambiar titular y cerrar ------------------------------------------------


def test_designar_otro_titular_principal(
    admin_client, unit, make_person, make_occupancy
):
    first = make_occupancy(unit, make_person("Uno"), "OWNER", is_primary=True)
    second = make_occupancy(unit, make_person("Dos"), "OWNER")

    response = admin_client.patch(detail(second), {"is_primary": True}, format="json")

    assert response.status_code == 200
    assert response.json()["is_primary"] is True
    first.refresh_from_db()
    assert first.is_primary is False


def test_un_inquilino_no_puede_ser_designado_titular(
    admin_client, unit, make_person, make_occupancy
):
    tenant = make_occupancy(unit, make_person("Inq"), "TENANT")
    response = admin_client.patch(detail(tenant), {"is_primary": True}, format="json")
    assert response.status_code == 400


def test_quitar_el_titular_sin_reemplazo_devuelve_400(
    admin_client, unit, make_person, make_occupancy
):
    holder = make_occupancy(unit, make_person("Uno"), "OWNER", is_primary=True)
    response = admin_client.patch(detail(holder), {"is_primary": False}, format="json")
    assert response.status_code == 400
    holder.refresh_from_db()
    assert holder.is_primary is True


def test_cerrar_una_ocupacion(admin_client, unit, make_person, make_occupancy):
    occupancy = make_occupancy(unit, make_person("Uno"), "OWNER")

    response = admin_client.patch(
        detail(occupancy), {"end_date": "2025-06-30"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["end_date"] == "2025-06-30"
    assert response.json()["is_active"] is False
    assert AuditLog.objects.filter(action="OCCUPANCY_CLOSED").count() == 1


def test_cerrar_con_fecha_futura_devuelve_400(
    admin_client, unit, make_person, make_occupancy
):
    occupancy = make_occupancy(unit, make_person("Uno"), "OWNER")
    future = (timezone.localdate() + timedelta(days=2)).isoformat()
    response = admin_client.patch(
        detail(occupancy), {"end_date": future}, format="json"
    )
    assert response.status_code == 400


def test_cerrar_dos_veces_devuelve_400(admin_client, unit, make_person, make_occupancy):
    occupancy = make_occupancy(
        unit, make_person("Uno"), "OWNER", end_date=date(2024, 6, 1)
    )
    response = admin_client.patch(
        detail(occupancy), {"end_date": "2024-07-01"}, format="json"
    )
    assert response.status_code == 400


def test_designar_titular_en_una_ocupacion_cerrada_devuelve_400(
    admin_client, unit, make_person, make_occupancy
):
    old = make_occupancy(unit, make_person("Uno"), "OWNER", end_date=date(2024, 6, 1))
    response = admin_client.patch(detail(old), {"is_primary": True}, format="json")
    assert response.status_code == 400


def test_cuerpo_vacio_o_con_ambos_campos_devuelve_400(
    admin_client, unit, make_person, make_occupancy
):
    occupancy = make_occupancy(unit, make_person("Uno"), "OWNER")
    assert admin_client.patch(detail(occupancy), {}, format="json").status_code == 400
    both = {"is_primary": True, "end_date": "2025-01-01"}
    assert admin_client.patch(detail(occupancy), both, format="json").status_code == 400


def test_solo_el_admin_modifica_ocupaciones(owner, unit, make_person, make_occupancy):
    other = make_occupancy(unit, make_person("Otro"), "OWNER")
    response = client_for(owner).patch(
        detail(other), {"is_primary": True}, format="json"
    )
    assert response.status_code == 403


def test_ocupacion_inexistente_devuelve_404(admin_client):
    response = admin_client.patch(
        f"/api/v1/occupancies/{MISSING}/", {"is_primary": True}, format="json"
    )
    assert response.status_code == 404
