"""S3-A: cargos manuales y anulación."""

from datetime import date
from decimal import Decimal

import pytest

from apps.core.models import AuditLog
from apps.finances import services
from apps.finances.models import Charge
from apps.finances.services import (
    ChargeAlreadyExistsError,
    ChargeHasAllocationsError,
    ChargeNotOpenError,
    InvalidChargeError,
    create_manual_charge,
    generate_monthly_charges,
    void_charge,
)

pytestmark = pytest.mark.django_db

MARZO = date(2025, 3, 1)


# --- create_manual_charge ----------------------------------------------------


def test_crea_un_cargo_extraordinario_manual(unit, admin):
    cargo = create_manual_charge(
        unit,
        "EXTRAORDINARY_FEE",
        date(2025, 3, 12),
        "150.00",
        description="Reparación del portón",
        actor=admin,
        ip="10.0.0.5",
    )
    cargo.refresh_from_db()
    assert cargo.source == "MANUAL"
    assert cargo.status == "OPEN"
    assert cargo.amount == Decimal("150.00")
    assert cargo.period == MARZO
    assert cargo.due_date == date(2025, 3, 31)
    assert cargo.description == "Reparación del portón"
    evento = AuditLog.objects.get(action="CHARGE_CREATED")
    assert evento.entity_type == "Charge"
    assert evento.entity_id == str(cargo.pk)
    assert evento.actor_label == admin.email
    assert evento.changes == {
        "unit": "101",
        "concept": "EXTRAORDINARY_FEE",
        "period": "2025-03-01",
        "amount": "150.00",
        "source": "MANUAL",
    }


def test_acepta_decimal_texto_y_entero(unit):
    for i, monto in enumerate((Decimal("10.50"), "20", 30)):
        cargo = create_manual_charge(unit, "ADJUSTMENT", date(2025, 3 + i, 1), monto)
        assert cargo.amount == Decimal(str(monto)).quantize(Decimal("0.01"))


def test_la_fecha_de_vencimiento_puede_indicarse(unit):
    cargo = create_manual_charge(
        unit, "EXTRAORDINARY_FEE", MARZO, "50.00", due_date=date(2025, 4, 15)
    )
    assert cargo.due_date == date(2025, 4, 15)


def test_el_vencimiento_no_puede_ser_anterior_al_periodo(unit):
    with pytest.raises(InvalidChargeError):
        create_manual_charge(
            unit, "EXTRAORDINARY_FEE", MARZO, "50.00", due_date=date(2025, 2, 28)
        )


@pytest.mark.parametrize("monto", ["0", "-10.00", "10.005", "abc", None, 12.5, True])
def test_rechaza_montos_invalidos(unit, monto):
    with pytest.raises(InvalidChargeError):
        create_manual_charge(unit, "EXTRAORDINARY_FEE", MARZO, monto)
    assert Charge.objects.count() == 0


@pytest.mark.parametrize("concepto", ["AGREEMENT_INSTALLMENT", "LATE_FEE", "OTRO"])
def test_rechaza_conceptos_que_no_son_manuales(unit, concepto):
    with pytest.raises(InvalidChargeError):
        create_manual_charge(unit, concepto, MARZO, "10.00")


def test_rechaza_cargos_a_unidades_inactivas(tower, make_unit):
    inactiva = make_unit(tower, "109", is_active=False)
    with pytest.raises(InvalidChargeError):
        create_manual_charge(inactiva, "EXTRAORDINARY_FEE", MARZO, "10.00")


def test_la_cuota_ordinaria_manual_no_duplica_la_del_sistema(tower, unit, fee_rate):
    generate_monthly_charges(MARZO, tower=tower)
    with pytest.raises(ChargeAlreadyExistsError):
        create_manual_charge(unit, "ORDINARY_FEE", MARZO, "80.00")


def test_la_cuota_ordinaria_manual_sirve_si_el_periodo_esta_libre(unit):
    cargo = create_manual_charge(unit, "ORDINARY_FEE", MARZO, "80.00")
    assert cargo.concept == "ORDINARY_FEE"
    assert cargo.source == "MANUAL"


def test_los_cargos_extraordinarios_pueden_repetirse(unit):
    create_manual_charge(unit, "EXTRAORDINARY_FEE", MARZO, "50.00")
    create_manual_charge(unit, "EXTRAORDINARY_FEE", MARZO, "50.00")
    assert Charge.objects.count() == 2


# --- void_charge -------------------------------------------------------------


@pytest.fixture
def cargo(unit, make_charge):
    return make_charge(unit, MARZO)


def test_anular_cambia_el_estado_y_no_borra(cargo, admin):
    void_charge(cargo, "Cobro duplicado", actor=admin, ip="10.0.0.5")
    cargo.refresh_from_db()
    assert cargo.status == "VOID"
    assert Charge.objects.filter(pk=cargo.pk).exists()


def test_anular_audita_con_el_motivo(cargo, admin):
    void_charge(cargo, "  Cobro duplicado  ", actor=admin)
    evento = AuditLog.objects.get(action="CHARGE_VOIDED")
    assert evento.entity_id == str(cargo.pk)
    assert evento.actor_label == admin.email
    assert evento.changes == {
        "unit": "101",
        "concept": "ORDINARY_FEE",
        "period": "2025-03-01",
        "amount": "80.00",
        "reason": "Cobro duplicado",
    }


@pytest.mark.parametrize("motivo", ["", "   ", None])
def test_anular_exige_un_motivo(cargo, motivo):
    with pytest.raises(InvalidChargeError):
        void_charge(cargo, motivo)
    cargo.refresh_from_db()
    assert cargo.status == "OPEN"


def test_no_se_anula_dos_veces(cargo):
    void_charge(cargo, "Error")
    with pytest.raises(ChargeNotOpenError):
        void_charge(cargo, "Otra vez")
    assert AuditLog.objects.filter(action="CHARGE_VOIDED").count() == 1


def test_no_se_anula_un_cargo_refinanciado(unit, make_charge):
    refinanciado = make_charge(unit, MARZO, status="REFINANCED")
    with pytest.raises(ChargeNotOpenError):
        void_charge(refinanciado, "Error")


def test_no_se_anula_un_cargo_con_pagos_aplicados(cargo, monkeypatch):
    monkeypatch.setattr(
        services, "allocated_totals", lambda ids: {cargo.pk: Decimal("30.00")}
    )
    with pytest.raises(ChargeHasAllocationsError):
        void_charge(cargo, "Error")
    cargo.refresh_from_db()
    assert cargo.status == "OPEN"


def test_una_cuota_anulada_no_se_regenera(tower, unit, fee_rate):
    generate_monthly_charges(MARZO, tower=tower)
    void_charge(Charge.objects.get(unit=unit), "Unidad exonerada")
    resultado = generate_monthly_charges(MARZO, tower=tower)
    assert (resultado.created, resultado.existing) == (0, 1)
    assert Charge.objects.get(unit=unit).status == "VOID"
