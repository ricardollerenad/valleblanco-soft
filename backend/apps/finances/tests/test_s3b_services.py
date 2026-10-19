"""S3-B: servicios nuevos (tarifas, pendientes y estado de cuenta)."""

from datetime import date
from decimal import Decimal

import pytest

from apps.core.models import AuditLog
from apps.finances import services
from apps.finances.models import FeeRate, FinanceSettings

pytestmark = pytest.mark.django_db


# --- create_fee_rate ---------------------------------------------------------


def test_crear_tarifa_cierra_la_abierta_y_audita(tower, fee_rate, admin):
    nueva = services.create_fee_rate(
        tower, "ORDINARY_FEE", "90.00", date(2025, 6, 1), actor=admin, ip="10.0.0.5"
    )

    fee_rate.refresh_from_db()
    assert fee_rate.valid_to == date(2025, 5, 31)
    assert nueva.amount == Decimal("90.00")
    assert nueva.valid_to is None
    evento = AuditLog.objects.get(action="FEE_RATE_CREATED")
    assert evento.ip_address == "10.0.0.5"
    assert evento.changes["closed_previous"] == str(fee_rate.pk)


def test_crear_la_primera_tarifa_no_cierra_nada(tower, admin):
    services.create_fee_rate(tower, "ORDINARY_FEE", "80", date(2025, 1, 1), actor=admin)

    evento = AuditLog.objects.get(action="FEE_RATE_CREATED")
    assert evento.changes["closed_previous"] is None


def test_no_cierra_una_tarifa_que_ya_tenia_fin(tower, make_fee_rate, admin):
    vieja = make_fee_rate(
        tower, valid_from=date(2020, 1, 1), valid_to=date(2020, 12, 31)
    )

    services.create_fee_rate(tower, "ORDINARY_FEE", "90", date(2025, 6, 1), actor=admin)

    vieja.refresh_from_db()
    assert vieja.valid_to == date(2020, 12, 31)


def test_rechaza_inicio_no_posterior_a_la_ultima_tarifa(tower, fee_rate):
    with pytest.raises(services.InvalidFeeRateError):
        services.create_fee_rate(tower, "ORDINARY_FEE", "90", date(2020, 1, 1))

    assert FeeRate.objects.count() == 1


def test_rechaza_una_tarifa_que_se_superpone_con_una_cerrada(tower, make_fee_rate):
    make_fee_rate(tower, valid_from=date(2020, 1, 1), valid_to=date(2025, 12, 31))

    with pytest.raises(services.InvalidFeeRateError):
        services.create_fee_rate(tower, "ORDINARY_FEE", "90", date(2025, 6, 1))

    assert FeeRate.objects.count() == 1


@pytest.mark.parametrize("monto", [0, "0", "-1", "1.234", "x", None, 80.5, True])
def test_rechaza_montos_invalidos(tower, monto):
    with pytest.raises(services.InvalidFeeRateError):
        services.create_fee_rate(tower, "ORDINARY_FEE", monto, date(2025, 1, 1))


def test_rechaza_conceptos_sin_tarifa(tower):
    with pytest.raises(services.InvalidFeeRateError):
        services.create_fee_rate(tower, "LATE_FEE", "10", date(2025, 1, 1))


def test_rechaza_fin_anterior_al_inicio(tower):
    with pytest.raises(services.InvalidFeeRateError):
        services.create_fee_rate(
            tower, "ORDINARY_FEE", "10", date(2025, 6, 1), valid_to=date(2025, 5, 1)
        )


# --- pending_amounts ---------------------------------------------------------


def test_pending_amounts_devuelve_el_pendiente_de_cada_cargo(
    unit, make_charge, monkeypatch
):
    a = make_charge(unit, date(2025, 1, 1))
    b = make_charge(unit, date(2025, 2, 1))
    monkeypatch.setattr(
        services, "allocated_totals", lambda ids: {a.pk: Decimal("80.00")}
    )

    assert services.pending_amounts([a, b]) == {
        a.pk: Decimal("0.00"),
        b.pk: Decimal("80.00"),
    }


def test_pending_amounts_es_cero_para_cargos_que_no_estan_abiertos(unit, make_charge):
    anulado = make_charge(unit, date(2025, 1, 1), status="VOID")
    refinanciado = make_charge(unit, date(2025, 2, 1), status="REFINANCED")

    assert services.pending_amounts([anulado, refinanciado]) == {
        anulado.pk: Decimal("0.00"),
        refinanciado.pk: Decimal("0.00"),
    }


# --- unit_statement ----------------------------------------------------------


def test_estado_de_cuenta_de_una_unidad(tower, unit, make_charge):
    make_charge(unit, date(2025, 1, 1))
    make_charge(unit, date(2025, 2, 1))
    make_charge(unit, date(2025, 3, 1), status="VOID")
    make_charge(unit, date(2025, 4, 1))
    FinanceSettings.objects.create(tower=tower, delinquency_threshold=2)

    estado = services.unit_statement(unit, as_of=date(2025, 3, 10))

    assert estado.unit == unit
    assert estado.as_of == date(2025, 3, 10)
    assert [line.charge.period for line in estado.lines] == [
        date(2025, 1, 1),
        date(2025, 2, 1),
        date(2025, 4, 1),
    ]
    assert [line.is_overdue for line in estado.lines] == [True, True, False]
    assert estado.pending_balance == Decimal("240.00")
    assert estado.overdue_balance == Decimal("160.00")
    assert estado.months_of_debt == 2
    assert estado.threshold == 2
    assert estado.is_delinquent is True


def test_estado_de_cuenta_sin_as_of_usa_hoy(unit):
    from django.utils import timezone

    assert services.unit_statement(unit).as_of == timezone.localdate()
