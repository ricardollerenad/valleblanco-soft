"""S3-B: GET /reports/delinquency/."""

from datetime import date

import pytest

from apps.finances.models import FinanceSettings

pytestmark = pytest.mark.django_db

URL = "/api/v1/reports/delinquency/"
AS_OF = {"as_of": "2025-06-15"}


@pytest.fixture
def morosos(unit, unit2, make_charge):
    """La 101 debe 4 meses (moroso) y la 102 debe 3 (no lo es con umbral 4)."""
    for mes in (1, 2, 3, 4):
        make_charge(unit, date(2025, mes, 1))
    for mes in (1, 2, 3):
        make_charge(unit2, date(2025, mes, 1))


@pytest.mark.parametrize("rol", ["admin", "admin_rol", "junta", "tesorero"])
def test_la_gestion_ve_las_unidades_morosas(actores, morosos, rol):
    response = actores[rol].get(URL, AS_OF)

    assert response.status_code == 200
    data = response.json()
    assert data["as_of"] == "2025-06-15"
    assert data["count"] == 1
    assert data["total_overdue"] == "320.00"
    moroso = data["results"][0]
    assert moroso["unit_code"] == "101"
    assert moroso["months"] == 4
    assert moroso["overdue_amount"] == "320.00"


def test_cada_resultado_identifica_unidad_y_torre(admin_client, tower, unit, morosos):
    moroso = admin_client.get(URL, AS_OF).json()["results"][0]

    assert moroso["unit"] == str(unit.pk)
    assert moroso["tower"] == str(tower.pk)
    assert moroso["tower_name"] == "Torre 2"


def test_informa_el_umbral_de_cada_torre(admin_client, tower, morosos):
    towers = admin_client.get(URL, AS_OF).json()["towers"]

    assert towers == [
        {"id": str(tower.pk), "name": "Torre 2", "delinquency_threshold": 4}
    ]


def test_respeta_el_umbral_configurado_y_ordena_por_deuda(admin_client, tower, morosos):
    FinanceSettings.objects.create(tower=tower, delinquency_threshold=3)

    data = admin_client.get(URL, AS_OF).json()

    assert [r["unit_code"] for r in data["results"]] == ["101", "102"]
    assert data["count"] == 2
    assert data["total_overdue"] == "560.00"


def test_sin_morosos_el_reporte_esta_vacio(admin_client, unit):
    data = admin_client.get(URL, AS_OF).json()

    assert data["count"] == 0
    assert data["results"] == []
    assert data["total_overdue"] == "0.00"


def test_filtra_por_torre(admin_client, other_tower, morosos):
    assert (
        admin_client.get(URL, {**AS_OF, "tower": str(other_tower.pk)}).json()["count"]
        == 0
    )


def test_filtro_de_torre_con_formato_invalido_devuelve_400(admin_client):
    response = admin_client.get(URL, {"tower": "no-es-uuid"})

    assert response.status_code == 400
    assert "tower" in response.json()["errors"]


def test_torre_inexistente_devuelve_404(admin_client):
    response = admin_client.get(URL, {"tower": "00000000-0000-4000-8000-000000000000"})

    assert response.status_code == 404


def test_as_of_invalido_devuelve_400(admin_client):
    response = admin_client.get(URL, {"as_of": "ayer"})

    assert response.status_code == 400
    assert "as_of" in response.json()["errors"]


def test_sin_as_of_se_usa_hoy(admin_client, unit, make_charge):
    for mes in (1, 2, 3, 4):
        make_charge(unit, date(2020, mes, 1))

    assert admin_client.get(URL).json()["count"] == 1


@pytest.mark.parametrize("rol", ["propietario", "inquilino", "vecino"])
def test_los_residentes_no_acceden_al_reporte(actores, morosos, rol):
    response = actores[rol].get(URL, AS_OF)

    assert response.status_code == 403
    assert "320.00" not in response.content.decode()


def test_anonimo_no_accede_al_reporte(actores):
    assert actores["anonimo"].get(URL).status_code in (401, 403)
