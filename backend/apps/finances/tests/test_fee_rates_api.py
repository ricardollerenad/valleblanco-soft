"""S3-B: tarifas (GET/POST /fee-rates/)."""

from datetime import date
from decimal import Decimal

import pytest

from apps.core.models import AuditLog
from apps.finances.models import FeeRate

pytestmark = pytest.mark.django_db

URL = "/api/v1/fee-rates/"


def cuerpo(tower, **extra):
    data = {
        "tower": str(tower.pk),
        "concept": "ORDINARY_FEE",
        "amount": "90.00",
        "valid_from": "2025-06-01",
    }
    data.update(extra)
    return data


# --- Lectura -----------------------------------------------------------------


def test_anonimo_no_puede_listar_tarifas(actores):
    assert actores["anonimo"].get(URL).status_code in (401, 403)


def test_cualquier_autenticado_lista_tarifas_con_dinero_como_texto(actores, fee_rate):
    response = actores["propietario"].get(URL)

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    tarifa = data["results"][0]
    assert tarifa["amount"] == "80.00"
    assert tarifa["concept"] == "ORDINARY_FEE"
    assert tarifa["valid_from"] == "2020-01-01"
    assert tarifa["valid_to"] is None
    assert tarifa["tower"] == str(fee_rate.tower_id)


def test_filtra_tarifas_por_torre(admin_client, tower, other_tower, make_fee_rate):
    make_fee_rate(tower)
    make_fee_rate(other_tower, amount="70.00")

    data = admin_client.get(URL, {"tower": str(other_tower.pk)}).json()

    assert data["count"] == 1
    assert data["results"][0]["amount"] == "70.00"


def test_filtra_tarifas_por_concepto(admin_client, fee_rate):
    assert admin_client.get(URL, {"concept": "ORDINARY_FEE"}).json()["count"] == 1
    assert admin_client.get(URL, {"concept": "ADJUSTMENT"}).json()["count"] == 0


def test_filtro_de_torre_invalido_devuelve_400(admin_client):
    response = admin_client.get(URL, {"tower": "no-es-uuid"})

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "tower" in response.json()["errors"]


def test_filtro_de_concepto_invalido_devuelve_400(admin_client):
    response = admin_client.get(URL, {"concept": "INVENTADO"})

    assert response.status_code == 400
    assert "concept" in response.json()["errors"]


# --- Alta --------------------------------------------------------------------


def test_admin_crea_una_tarifa(admin_client, tower):
    response = admin_client.post(URL, cuerpo(tower), format="json")

    assert response.status_code == 201
    data = response.json()
    assert data["amount"] == "90.00"
    assert data["valid_from"] == "2025-06-01"
    assert data["valid_to"] is None
    tarifa = FeeRate.objects.get(pk=data["id"])
    assert tarifa.amount == Decimal("90.00")
    assert tarifa.tower == tower


def test_crear_una_tarifa_audita_con_la_ip(admin_client, tower, admin):
    admin_client.post(URL, cuerpo(tower), format="json")

    evento = AuditLog.objects.get(action="FEE_RATE_CREATED")
    assert evento.entity_type == "FeeRate"
    assert evento.actor_id == str(admin.pk)
    assert evento.ip_address == "127.0.0.1"
    assert evento.changes["amount"] == "90.00"
    assert evento.changes["valid_from"] == "2025-06-01"


def test_la_nueva_tarifa_cierra_la_anterior_abierta(admin_client, tower, fee_rate):
    admin_client.post(URL, cuerpo(tower), format="json")

    fee_rate.refresh_from_db()
    assert fee_rate.valid_to == date(2025, 5, 31)


def test_la_nueva_tarifa_no_altera_cargos_ya_emitidos(
    admin_client, tower, unit, fee_rate, make_charge
):
    cargo = make_charge(unit, date(2025, 3, 1))

    admin_client.post(URL, cuerpo(tower), format="json")

    cargo.refresh_from_db()
    assert cargo.amount == Decimal("80.00")


def test_la_tarifa_nueva_rige_al_generar_los_cargos_de_su_periodo(
    admin_client, tower, unit, fee_rate
):
    admin_client.post(URL, cuerpo(tower), format="json")

    mayo = admin_client.post(
        "/api/v1/charges/generate/", {"period": "2025-05"}, format="json"
    )
    julio = admin_client.post(
        "/api/v1/charges/generate/", {"period": "2025-07"}, format="json"
    )

    assert mayo.status_code == julio.status_code == 200
    from apps.finances.models import Charge

    assert Charge.objects.get(unit=unit, period=date(2025, 5, 1)).amount == Decimal(
        "80.00"
    )
    assert Charge.objects.get(unit=unit, period=date(2025, 7, 1)).amount == Decimal(
        "90.00"
    )


def test_una_tarifa_de_otra_torre_no_cierra_la_de_esta(
    admin_client, tower, other_tower, fee_rate
):
    admin_client.post(URL, cuerpo(other_tower), format="json")

    fee_rate.refresh_from_db()
    assert fee_rate.valid_to is None


@pytest.mark.parametrize("inicio", ["2020-01-01", "2019-12-01"])
def test_rechaza_una_tarifa_que_no_empieza_despues_de_la_ultima(
    admin_client, tower, fee_rate, inicio
):
    response = admin_client.post(URL, cuerpo(tower, valid_from=inicio), format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_fee_rate"
    assert FeeRate.objects.count() == 1


@pytest.mark.parametrize("monto", ["0", "0.00", "-5.00", "10.123", "abc", ""])
def test_rechaza_montos_invalidos(admin_client, tower, monto):
    response = admin_client.post(URL, cuerpo(tower, amount=monto), format="json")

    assert response.status_code == 400
    assert not FeeRate.objects.exists()


@pytest.mark.parametrize("concepto", ["EXTRAORDINARY_FEE", "LATE_FEE", "INVENTADO"])
def test_solo_se_admite_tarifa_de_cuota_ordinaria(admin_client, tower, concepto):
    response = admin_client.post(URL, cuerpo(tower, concept=concepto), format="json")

    assert response.status_code == 400
    assert "concept" in response.json()["errors"]


def test_rechaza_fin_anterior_al_inicio(admin_client, tower):
    response = admin_client.post(
        URL, cuerpo(tower, valid_to="2025-05-31"), format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_fee_rate"


def test_acepta_una_tarifa_con_fin(admin_client, tower):
    response = admin_client.post(
        URL, cuerpo(tower, valid_to="2025-12-31"), format="json"
    )

    assert response.status_code == 201
    assert response.json()["valid_to"] == "2025-12-31"


def test_torre_inexistente_devuelve_400(admin_client):
    response = admin_client.post(
        URL,
        {
            "tower": "00000000-0000-4000-8000-000000000000",
            "concept": "ORDINARY_FEE",
            "amount": "90.00",
            "valid_from": "2025-06-01",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "tower" in response.json()["errors"]


def test_faltan_campos_obligatorios(admin_client):
    response = admin_client.post(URL, {}, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert {"tower", "concept", "amount", "valid_from"} <= set(
        response.json()["errors"]
    )
