"""S3-A: saldo pendiente, meses de deuda y morosidad."""

from datetime import date
from decimal import Decimal

import pytest

from apps.finances import services
from apps.finances.models import FinanceSettings
from apps.finances.services import (
    delinquency_threshold,
    delinquent_units,
    get_or_create_settings,
    is_delinquent,
    months_of_debt,
    overdue_balance,
    pending_amount,
    pending_balance,
)

pytestmark = pytest.mark.django_db

HOY = date(2025, 6, 15)


@pytest.fixture
def deuda(unit, make_charge):
    """Cuatro cuotas vencidas (ene-abr) y una por vencer (jun, vence el 28)."""
    return [
        make_charge(unit, date(2025, mes, 1), due_date=date(2025, mes, 28))
        for mes in (1, 2, 3, 4, 6)
    ]


def test_saldo_pendiente_suma_los_cargos_abiertos(unit, deuda):
    assert pending_balance(unit) == Decimal("400.00")


def test_el_saldo_ignora_cargos_anulados_y_refinanciados(unit, make_charge):
    make_charge(unit, date(2025, 1, 1))
    make_charge(unit, date(2025, 2, 1), status="VOID")
    make_charge(unit, date(2025, 3, 1), status="REFINANCED")
    assert pending_balance(unit) == Decimal("80.00")


def test_el_saldo_de_una_unidad_no_incluye_a_otra(unit, unit2, make_charge):
    make_charge(unit, date(2025, 1, 1))
    make_charge(unit2, date(2025, 1, 1), amount="500.00")
    assert pending_balance(unit) == Decimal("80.00")


def test_unidad_sin_cargos_tiene_saldo_cero(unit):
    assert pending_balance(unit) == Decimal("0.00")
    assert months_of_debt(unit, as_of=HOY) == 0


def test_los_pagos_aplicados_reducen_el_saldo(unit, deuda, monkeypatch):
    aplicado = {deuda[0].pk: Decimal("80.00"), deuda[1].pk: Decimal("30.00")}
    monkeypatch.setattr(services, "allocated_totals", lambda ids: aplicado)
    assert pending_balance(unit) == Decimal("290.00")
    assert pending_amount(deuda[1]) == Decimal("50.00")
    assert pending_amount(deuda[0]) == Decimal("0.00")


def test_saldo_vencido_solo_cuenta_lo_que_ya_vencio(unit, deuda):
    assert overdue_balance(unit, as_of=HOY) == Decimal("320.00")
    assert overdue_balance(unit, as_of=date(2025, 6, 29)) == Decimal("400.00")


def test_meses_de_deuda_cuenta_cargos_vencidos_con_saldo(unit, deuda):
    assert months_of_debt(unit, as_of=HOY) == 4


def test_un_cargo_vence_despues_de_su_fecha_limite(unit, make_charge):
    make_charge(unit, date(2025, 5, 1), due_date=date(2025, 5, 31))
    assert months_of_debt(unit, as_of=date(2025, 5, 31)) == 0
    assert months_of_debt(unit, as_of=date(2025, 6, 1)) == 1


def test_un_cargo_pagado_por_completo_no_cuenta_como_deuda(unit, deuda, monkeypatch):
    monkeypatch.setattr(
        services, "allocated_totals", lambda ids: {deuda[0].pk: Decimal("80.00")}
    )
    assert months_of_debt(unit, as_of=HOY) == 3


def test_un_pago_parcial_sigue_contando_como_mes_de_deuda(unit, deuda, monkeypatch):
    monkeypatch.setattr(
        services, "allocated_totals", lambda ids: {deuda[0].pk: Decimal("79.99")}
    )
    assert months_of_debt(unit, as_of=HOY) == 4


def test_los_cargos_anulados_no_cuentan_como_deuda(unit, make_charge):
    make_charge(unit, date(2025, 1, 1), status="VOID")
    assert months_of_debt(unit, as_of=HOY) == 0


def test_umbral_por_defecto_es_cuatro(tower):
    assert delinquency_threshold(tower) == 4
    assert not FinanceSettings.objects.exists()


def test_es_moroso_con_cuatro_o_mas_meses(unit, make_charge):
    for mes in (1, 2, 3):
        make_charge(unit, date(2025, mes, 1), due_date=date(2025, mes, 28))
    assert not is_delinquent(unit, as_of=HOY)
    make_charge(unit, date(2025, 4, 1), due_date=date(2025, 4, 28))
    assert is_delinquent(unit, as_of=HOY)


def test_el_umbral_es_configurable(tower, unit, make_charge):
    settings_ = get_or_create_settings(tower)
    settings_.delinquency_threshold = 2
    settings_.save()
    for mes in (1, 2):
        make_charge(unit, date(2025, mes, 1), due_date=date(2025, mes, 28))
    assert is_delinquent(unit, as_of=HOY)


def test_get_or_create_settings_no_duplica(tower):
    primero = get_or_create_settings(tower)
    assert get_or_create_settings(tower).pk == primero.pk
    assert FinanceSettings.objects.count() == 1


def test_lista_de_unidades_morosas(tower, unit, unit2, make_unit, make_charge):
    tercera = make_unit(tower, "103")
    for mes in (1, 2, 3, 4, 5):
        make_charge(unit, date(2025, mes, 1), due_date=date(2025, mes, 28))
    for mes in (1, 2, 3, 4):
        make_charge(unit2, date(2025, mes, 1), due_date=date(2025, mes, 28))
    for mes in (1, 2):
        make_charge(tercera, date(2025, mes, 1), due_date=date(2025, mes, 28))
    lista = delinquent_units(tower, as_of=HOY)
    assert [(item.unit.code, item.months, item.overdue_amount) for item in lista] == [
        ("101", 5, Decimal("400.00")),
        ("102", 4, Decimal("320.00")),
    ]


def test_la_lista_de_morosos_respeta_la_torre(
    tower, other_tower, unit, make_unit, make_charge
):
    ajena = make_unit(other_tower, "101")
    for mes in (1, 2, 3, 4):
        make_charge(ajena, date(2025, mes, 1), due_date=date(2025, mes, 28))
    assert delinquent_units(tower, as_of=HOY) == []
    assert len(delinquent_units(other_tower, as_of=HOY)) == 1
