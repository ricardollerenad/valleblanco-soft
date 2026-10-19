"""S3-B: POST /charges/generate/ y POST /charges/{id}/void/."""

from datetime import date
from decimal import Decimal

import pytest

from apps.core.models import AuditLog
from apps.finances import services
from apps.finances.models import Charge

pytestmark = pytest.mark.django_db

GENERATE = "/api/v1/charges/generate/"
MARZO = date(2025, 3, 1)


def void_url(charge):
    return f"/api/v1/charges/{charge.pk}/void/"


# --- Generar -----------------------------------------------------------------


def test_admin_genera_las_cuotas_del_periodo(
    admin_client, tower, unit, unit2, make_unit, fee_rate
):
    make_unit(tower, "103", billing_start=None)

    response = admin_client.post(GENERATE, {"period": "2025-03"}, format="json")

    assert response.status_code == 200
    assert response.json() == {
        "period": "2025-03-01",
        "created": 2,
        "existing": 0,
        "skipped": 1,
    }
    cargos = Charge.objects.filter(period=MARZO)
    assert cargos.count() == 2
    assert {c.amount for c in cargos} == {Decimal("80.00")}
    assert {c.source for c in cargos} == {"SYSTEM"}
    assert {c.due_date for c in cargos} == {date(2025, 3, 31)}


def test_generar_dos_veces_no_duplica(admin_client, unit, unit2, fee_rate):
    admin_client.post(GENERATE, {"period": "2025-03"}, format="json")

    response = admin_client.post(GENERATE, {"period": "2025-03-01"}, format="json")

    assert response.status_code == 200
    assert response.json()["created"] == 0
    assert response.json()["existing"] == 2
    assert Charge.objects.count() == 2


def test_generar_audita_con_la_ip(admin_client, tower, unit, fee_rate, admin):
    admin_client.post(GENERATE, {"period": "2025-03"}, format="json")

    evento = AuditLog.objects.get(action="CHARGES_GENERATED")
    assert evento.entity_id == str(tower.pk)
    assert evento.actor_id == str(admin.pk)
    assert evento.ip_address == "127.0.0.1"
    assert evento.changes["created"] == 1


def test_generar_sin_periodo_devuelve_400(admin_client, fee_rate):
    response = admin_client.post(GENERATE, {}, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "period" in response.json()["errors"]


def test_generar_con_periodo_invalido_devuelve_400(admin_client, fee_rate):
    response = admin_client.post(GENERATE, {"period": "marzo"}, format="json")

    assert response.status_code == 400
    assert "period" in response.json()["errors"]


def test_generar_un_mes_futuro_devuelve_400(admin_client, unit, fee_rate):
    response = admin_client.post(GENERATE, {"period": "2099-01"}, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_period"
    assert not Charge.objects.exists()


def test_sin_tarifa_vigente_devuelve_409_y_no_crea_nada(admin_client, unit):
    response = admin_client.post(GENERATE, {"period": "2025-03"}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "fee_rate_not_found"
    assert not Charge.objects.exists()


def test_generar_para_una_sola_torre(
    admin_client, tower, other_tower, unit, make_unit, fee_rate
):
    make_unit(other_tower, "101")  # la otra torre no tiene tarifa

    response = admin_client.post(
        GENERATE, {"period": "2025-03", "tower": str(tower.pk)}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["created"] == 1


def test_generar_para_todas_es_todo_o_nada(
    admin_client, other_tower, unit, make_unit, fee_rate
):
    make_unit(other_tower, "101")

    response = admin_client.post(GENERATE, {"period": "2025-03"}, format="json")

    assert response.status_code == 409
    assert not Charge.objects.exists()


def test_generar_con_una_torre_inexistente_devuelve_400(admin_client, fee_rate):
    response = admin_client.post(
        GENERATE,
        {"period": "2025-03", "tower": "00000000-0000-4000-8000-000000000000"},
        format="json",
    )

    assert response.status_code == 400
    assert "tower" in response.json()["errors"]


# --- Anular ------------------------------------------------------------------


def test_admin_anula_un_cargo_con_motivo(admin_client, unit, make_charge):
    cargo = make_charge(unit, MARZO)

    response = admin_client.post(
        void_url(cargo), {"reason": "Cobrado por error"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "VOID"
    cargo.refresh_from_db()
    assert cargo.status == "VOID"
    assert Charge.objects.filter(pk=cargo.pk).exists()  # se anula, no se borra


def test_anular_audita_con_motivo_e_ip(admin_client, unit, make_charge, admin):
    cargo = make_charge(unit, MARZO)

    admin_client.post(void_url(cargo), {"reason": "Cobrado por error"}, format="json")

    evento = AuditLog.objects.get(action="CHARGE_VOIDED")
    assert evento.entity_id == str(cargo.pk)
    assert evento.actor_id == str(admin.pk)
    assert evento.ip_address == "127.0.0.1"
    assert evento.changes["reason"] == "Cobrado por error"


@pytest.mark.parametrize("cuerpo", [{}, {"reason": ""}, {"reason": "   "}])
def test_anular_sin_motivo_devuelve_400(admin_client, unit, make_charge, cuerpo):
    cargo = make_charge(unit, MARZO)

    response = admin_client.post(void_url(cargo), cuerpo, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "reason" in response.json()["errors"]
    cargo.refresh_from_db()
    assert cargo.status == "OPEN"


def test_anular_un_cargo_ya_anulado_devuelve_409(admin_client, unit, make_charge):
    cargo = make_charge(unit, MARZO, status="VOID")

    response = admin_client.post(void_url(cargo), {"reason": "otra vez"}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "charge_not_open"


def test_anular_un_cargo_con_pagos_aplicados_devuelve_409(
    admin_client, unit, make_charge, monkeypatch
):
    cargo = make_charge(unit, MARZO)
    monkeypatch.setattr(
        services, "allocated_totals", lambda ids: {cargo.pk: Decimal("10.00")}
    )

    response = admin_client.post(void_url(cargo), {"reason": "error"}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "charge_has_allocations"
    cargo.refresh_from_db()
    assert cargo.status == "OPEN"


def test_anular_un_cargo_inexistente_devuelve_404(admin_client):
    response = admin_client.post(
        "/api/v1/charges/00000000-0000-4000-8000-000000000000/void/",
        {"reason": "x"},
        format="json",
    )

    assert response.status_code == 404
