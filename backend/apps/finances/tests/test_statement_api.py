"""S3-B: GET /units/{id}/statement/ (estado de cuenta en JSON)."""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.finances import services
from apps.finances.models import FinanceSettings

pytestmark = pytest.mark.django_db

AS_OF = {"as_of": "2025-03-10"}


def url(unit):
    return f"/api/v1/units/{unit.pk}/statement/"


@pytest.fixture
def tres_meses(unit, make_charge):
    """Enero y febrero vencidos y marzo por vencer al 10-mar-2025."""
    return [make_charge(unit, date(2025, m, 1)) for m in (1, 2, 3)]


def test_el_estado_de_cuenta_trae_unidad_resumen_y_lineas(actores, unit, tres_meses):
    response = actores["propietario"].get(url(unit), AS_OF)

    assert response.status_code == 200
    data = response.json()
    assert data["unit"]["id"] == str(unit.pk)
    assert data["unit"]["code"] == "101"
    assert data["unit"]["tower_name"] == "Torre 2"
    assert data["as_of"] == "2025-03-10"
    assert data["summary"] == {
        "pending_balance": "240.00",
        "overdue_balance": "160.00",
        "months_of_debt": 2,
        "delinquency_threshold": 4,
        "is_delinquent": False,
        "credit_balance": "0.00",
    }
    assert [c["period"] for c in data["charges"]] == [
        "2025-01-01",
        "2025-02-01",
        "2025-03-01",
    ]


def test_cada_linea_trae_monto_aplicado_pendiente_y_si_esta_vencida(
    actores, unit, tres_meses
):
    lineas = actores["propietario"].get(url(unit), AS_OF).json()["charges"]

    assert lineas[0] == {
        "id": str(tres_meses[0].pk),
        "concept": "ORDINARY_FEE",
        "period": "2025-01-01",
        "description": "",
        "amount": "80.00",
        "allocated": "0.00",
        "pending_amount": "80.00",
        "due_date": "2025-01-28",
        "status": "OPEN",
        "is_overdue": True,
    }
    assert lineas[2]["is_overdue"] is False


def test_un_pago_parcial_reduce_lo_pendiente(actores, unit, tres_meses, monkeypatch):
    monkeypatch.setattr(
        services,
        "allocated_totals",
        lambda ids: {tres_meses[0].pk: Decimal("30.00")},
    )

    data = actores["propietario"].get(url(unit), AS_OF).json()

    assert data["charges"][0]["allocated"] == "30.00"
    assert data["charges"][0]["pending_amount"] == "50.00"
    assert data["summary"]["pending_balance"] == "210.00"
    assert data["summary"]["overdue_balance"] == "130.00"


def test_los_cargos_anulados_no_aparecen_ni_suman(actores, unit, make_charge):
    make_charge(unit, date(2025, 1, 1), status="VOID")
    make_charge(unit, date(2025, 2, 1))

    data = actores["propietario"].get(url(unit), AS_OF).json()

    assert len(data["charges"]) == 1
    assert data["summary"]["pending_balance"] == "80.00"


def test_un_cargo_refinanciado_aparece_pero_no_debe_nada(actores, unit, make_charge):
    make_charge(unit, date(2025, 1, 1), status="REFINANCED")

    data = actores["propietario"].get(url(unit), AS_OF).json()

    assert data["charges"][0]["status"] == "REFINANCED"
    assert data["charges"][0]["pending_amount"] == "0.00"
    assert data["charges"][0]["is_overdue"] is False
    assert data["summary"]["pending_balance"] == "0.00"


def test_es_moroso_al_alcanzar_el_umbral_configurado(actores, tower, unit, tres_meses):
    FinanceSettings.objects.create(tower=tower, delinquency_threshold=2)

    summary = actores["propietario"].get(url(unit), AS_OF).json()["summary"]

    assert summary["delinquency_threshold"] == 2
    assert summary["is_delinquent"] is True


def test_una_unidad_sin_cargos_tiene_todo_en_cero(actores, unit):
    data = actores["propietario"].get(url(unit), AS_OF).json()

    assert data["charges"] == []
    assert data["summary"]["pending_balance"] == "0.00"
    assert data["summary"]["months_of_debt"] == 0


def test_sin_as_of_se_usa_la_fecha_de_hoy(actores, unit):
    data = actores["propietario"].get(url(unit)).json()

    assert data["as_of"] == timezone.localdate().isoformat()


@pytest.mark.parametrize("valor", ["ayer", "2025-13-01", "2025-03"])
def test_as_of_invalido_devuelve_400(actores, unit, valor):
    response = actores["propietario"].get(url(unit), {"as_of": valor})

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "as_of" in response.json()["errors"]


@pytest.mark.parametrize(
    "rol", ["admin", "admin_rol", "junta", "tesorero", "propietario", "inquilino"]
)
def test_pueden_ver_el_estado_de_la_unidad(actores, unit, tres_meses, rol):
    assert actores[rol].get(url(unit), AS_OF).status_code == 200


def test_otro_propietario_recibe_404_y_no_se_filtra_nada(actores, unit, tres_meses):
    response = actores["vecino"].get(url(unit), AS_OF)

    assert response.status_code == 404
    assert "240.00" not in response.content.decode()


def test_anonimo_no_puede_ver_el_estado(actores, unit):
    assert actores["anonimo"].get(url(unit), AS_OF).status_code in (401, 403)


def test_unidad_inexistente_devuelve_404(admin_client):
    response = admin_client.get(
        "/api/v1/units/00000000-0000-4000-8000-000000000000/statement/"
    )

    assert response.status_code == 404


def test_con_la_ocupacion_cerrada_el_acceso_se_pierde(unit, tres_meses, make_resident):
    from apps.finances.tests.conftest import client_for

    ex = make_resident("ex@valleblanco.pe", unit, "OWNER", end_date=date(2024, 12, 31))

    assert client_for(ex).get(url(unit), AS_OF).status_code == 404
