"""S2: GET/PATCH de unidades, con aislamiento entre unidades (IDOR)."""

import pytest
from rest_framework.test import APIClient

from apps.core.models import AuditLog
from apps.properties.models import Tower, Unit
from apps.properties.tests.conftest import client_for

pytestmark = pytest.mark.django_db

LIST = "/api/v1/units/"


def detail(unit):
    return f"/api/v1/units/{unit.pk}/"


@pytest.fixture
def edificio(tower):
    """30 unidades: pisos 1 a 3, 10 por piso."""
    return [
        Unit.objects.create(
            tower=tower, code=f"{floor}{number:02d}", floor=floor, number=number
        )
        for floor in (1, 2, 3)
        for number in range(1, 11)
    ]


# --- Listado -----------------------------------------------------------------


def test_anonimo_no_puede_listar():
    assert APIClient().get(LIST).status_code in (401, 403)


def test_admin_ve_todas_y_se_pagina_de_a_25(admin_client, edificio):
    data = admin_client.get(LIST).json()

    assert data["count"] == 30
    assert len(data["results"]) == 25
    assert data["next"] is not None
    second = admin_client.get(LIST, {"page": 2}).json()
    assert len(second["results"]) == 5


def test_las_unidades_salen_ordenadas_por_piso_y_numero(admin_client, edificio):
    codes = [u["code"] for u in admin_client.get(LIST).json()["results"]]
    assert codes[:3] == ["101", "102", "103"]


def test_filtra_por_piso(admin_client, edificio):
    data = admin_client.get(LIST, {"floor": 2}).json()
    assert data["count"] == 10
    assert {u["floor"] for u in data["results"]} == {2}


def test_piso_no_numerico_devuelve_400(admin_client, edificio):
    response = admin_client.get(LIST, {"floor": "dos"})
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_filtra_por_codigo(admin_client, edificio):
    data = admin_client.get(LIST, {"q": "305"}).json()
    assert [u["code"] for u in data["results"]] == ["305"]


def test_filtra_por_torre_y_una_torre_invalida_no_devuelve_nada(
    admin_client, edificio, tower
):
    other = Tower.objects.create(
        condominium=tower.condominium, name="Torre 3", floors=2
    )
    Unit.objects.create(tower=other, code="101", floor=1, number=1)

    assert admin_client.get(LIST, {"tower": str(other.pk)}).json()["count"] == 1
    assert admin_client.get(LIST, {"tower": "no-es-uuid"}).json()["count"] == 0


def test_filtra_por_activas(admin_client, edificio):
    Unit.objects.filter(code="101").update(is_active=False)
    assert admin_client.get(LIST, {"is_active": "false"}).json()["count"] == 1
    assert admin_client.get(LIST, {"is_active": "true"}).json()["count"] == 29


@pytest.mark.parametrize("role", ["BOARD", "TREASURER"])
def test_la_junta_ve_todas(edificio, make_user, give_role, role):
    user = make_user("junta@valleblanco.pe")
    give_role(user, role)
    assert client_for(user).get(LIST).json()["count"] == 30


def test_el_propietario_solo_ve_las_suyas(owner, unit2):
    data = client_for(owner).get(LIST).json()
    assert [u["code"] for u in data["results"]] == ["101"]


def test_el_inquilino_solo_ve_su_unidad(
    unit, unit2, make_user, make_person, make_occupancy
):
    user = make_user("inq@valleblanco.pe")
    make_occupancy(unit2, make_person("Inq", user=user), "TENANT")
    assert [u["code"] for u in client_for(user).get(LIST).json()["results"]] == ["102"]


def test_un_usuario_sin_ocupacion_no_ve_nada(edificio, make_user):
    user = make_user("nadie@valleblanco.pe")
    assert client_for(user).get(LIST).json()["count"] == 0


def test_una_ocupacion_cerrada_ya_no_da_acceso(
    unit, make_user, make_person, make_occupancy
):
    from datetime import date

    user = make_user("ex@valleblanco.pe")
    make_occupancy(unit, make_person("Ex", user=user), end_date=date(2025, 1, 1))
    assert client_for(user).get(LIST).json()["count"] == 0


def test_una_unidad_con_dos_ocupantes_no_se_repite(
    owner, unit, make_user, make_person, make_occupancy
):
    other = make_user("copropietario@valleblanco.pe")
    make_occupancy(unit, make_person("Co", user=other), "OWNER")
    assert client_for(owner).get(LIST).json()["count"] == 1


# --- Detalle (IDOR) ----------------------------------------------------------


def test_el_propietario_ve_el_detalle_de_su_unidad(owner, unit):
    response = client_for(owner).get(detail(unit))
    assert response.status_code == 200
    assert response.json()["code"] == "101"
    assert response.json()["tower_name"] == "Torre 2"


def test_el_propietario_no_ve_la_unidad_de_otro(owner, unit2):
    assert client_for(owner).get(detail(unit2)).status_code == 404


def test_el_admin_ve_cualquier_unidad(admin_client, unit2):
    assert admin_client.get(detail(unit2)).status_code == 200


def test_unidad_inexistente_devuelve_404(admin_client):
    assert (
        admin_client.get(
            "/api/v1/units/00000000-0000-4000-8000-000000000000/"
        ).status_code
        == 404
    )


# --- Edición -----------------------------------------------------------------


def test_admin_edita_inicio_de_cobro_y_estado(admin_client, unit):
    response = admin_client.patch(
        detail(unit), {"billing_start": "2024-03-01", "is_active": False}, format="json"
    )

    assert response.status_code == 200
    unit.refresh_from_db()
    assert str(unit.billing_start) == "2024-03-01"
    assert unit.is_active is False
    entry = AuditLog.objects.get(action="UNIT_UPDATED")
    assert entry.changes == {"fields": ["billing_start", "is_active"]}


def test_codigo_piso_y_torre_no_se_pueden_cambiar(admin_client, unit, tower):
    other = Tower.objects.create(
        condominium=tower.condominium, name="Torre 3", floors=2
    )

    admin_client.patch(
        detail(unit),
        {"code": "999", "floor": 9, "number": 9, "tower": str(other.pk)},
        format="json",
    )

    unit.refresh_from_db()
    assert (unit.code, unit.floor, unit.number, unit.tower) == ("101", 1, 1, tower)


def test_fecha_invalida_devuelve_400(admin_client, unit):
    response = admin_client.patch(
        detail(unit), {"billing_start": "ayer"}, format="json"
    )
    assert response.status_code == 400
    assert "billing_start" in response.json()["errors"]


def test_el_propietario_no_puede_editar_ni_su_propia_unidad(owner, unit):
    response = client_for(owner).patch(
        detail(unit), {"is_active": False}, format="json"
    )
    assert response.status_code == 403
    unit.refresh_from_db()
    assert unit.is_active is True


def test_la_junta_no_puede_editar(unit, make_user, give_role):
    user = make_user("junta@valleblanco.pe")
    give_role(user, "BOARD")
    assert (
        client_for(user)
        .patch(detail(unit), {"is_active": False}, format="json")
        .status_code
        == 403
    )


def test_put_y_delete_no_estan_permitidos(admin_client, unit):
    assert admin_client.put(detail(unit), {}, format="json").status_code == 405
    assert admin_client.delete(detail(unit)).status_code == 405
