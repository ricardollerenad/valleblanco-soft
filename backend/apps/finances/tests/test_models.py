"""S3-A: modelos de finanzas y sus restricciones en la base de datos."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.finances.models import Charge, FinanceSettings

pytestmark = pytest.mark.django_db


# --- FinanceSettings ---------------------------------------------------------


def test_configuracion_trae_los_valores_por_defecto(tower):
    settings_ = FinanceSettings.objects.create(tower=tower)
    assert settings_.due_rule == "END_OF_MONTH"
    assert settings_.delinquency_threshold == 4
    assert settings_.late_fee_enabled is False
    assert settings_.early_discount_enabled is False
    assert settings_.opening_balance == Decimal("0.00")
    assert settings_.opening_balance_date is None


def test_hay_una_sola_configuracion_por_torre(tower):
    FinanceSettings.objects.create(tower=tower)
    with pytest.raises(IntegrityError), transaction.atomic():
        FinanceSettings.objects.create(tower=tower)


def test_el_umbral_de_morosidad_es_al_menos_uno(tower):
    with pytest.raises(IntegrityError), transaction.atomic():
        FinanceSettings.objects.create(tower=tower, delinquency_threshold=0)


def test_la_mora_activada_exige_un_tipo(tower):
    with pytest.raises(IntegrityError), transaction.atomic():
        FinanceSettings.objects.create(tower=tower, late_fee_enabled=True)
    ok = FinanceSettings.objects.create(
        tower=tower,
        late_fee_enabled=True,
        late_fee_type="PERCENT",
        late_fee_value=Decimal("5.00"),
    )
    assert ok.late_fee_enabled


def test_el_valor_de_la_mora_no_puede_ser_negativo(tower):
    with pytest.raises(IntegrityError), transaction.atomic():
        FinanceSettings.objects.create(tower=tower, late_fee_value=Decimal("-1.00"))


# --- FeeRate -----------------------------------------------------------------


def test_tarifa_ordinaria_de_80_soles(fee_rate):
    assert fee_rate.concept == "ORDINARY_FEE"
    assert fee_rate.amount == Decimal("80.00")
    assert fee_rate.valid_to is None
    assert isinstance(fee_rate.pk, uuid.UUID)


def test_la_tarifa_debe_ser_positiva(tower, make_fee_rate):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_fee_rate(tower, amount="0.00")


def test_la_vigencia_no_termina_antes_de_empezar(tower, make_fee_rate):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_fee_rate(tower, valid_to=date(2019, 12, 31))


def test_no_se_repite_la_tarifa_con_el_mismo_inicio(tower, fee_rate, make_fee_rate):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_fee_rate(tower, amount="90.00")


# --- Charge ------------------------------------------------------------------


def test_cargo_nuevo_queda_abierto_y_manual_por_defecto(unit):
    charge = Charge.objects.create(
        unit=unit,
        concept="EXTRAORDINARY_FEE",
        period=date(2025, 3, 1),
        amount=Decimal("150.00"),
        due_date=date(2025, 3, 31),
    )
    assert charge.status == "OPEN"
    assert charge.source == "MANUAL"
    assert isinstance(charge.pk, uuid.UUID)


def test_el_monto_del_cargo_debe_ser_positivo(unit, make_charge):
    for monto in ("0.00", "-5.00"):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_charge(unit, date(2025, 3, 1), amount=monto)


def test_el_periodo_es_el_primer_dia_del_mes(unit, make_charge):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_charge(unit, date(2025, 3, 15), due_date=date(2025, 3, 31))


def test_el_vencimiento_no_es_anterior_al_periodo(unit, make_charge):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_charge(unit, date(2025, 3, 1), due_date=date(2025, 2, 28))


def test_la_cuota_ordinaria_es_unica_por_unidad_y_periodo(unit, make_charge):
    make_charge(unit, date(2025, 3, 1))
    with pytest.raises(IntegrityError), transaction.atomic():
        make_charge(unit, date(2025, 3, 1))


def test_una_cuota_anulada_sigue_ocupando_el_periodo(unit, make_charge):
    make_charge(unit, date(2025, 3, 1), status="VOID")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_charge(unit, date(2025, 3, 1))


def test_otros_conceptos_pueden_repetirse_en_el_mismo_periodo(unit, make_charge):
    make_charge(unit, date(2025, 3, 1), concept="EXTRAORDINARY_FEE")
    make_charge(unit, date(2025, 3, 1), concept="EXTRAORDINARY_FEE")
    assert Charge.objects.filter(unit=unit).count() == 2


def test_la_cuota_del_mismo_periodo_en_otra_unidad_es_valida(unit, unit2, make_charge):
    make_charge(unit, date(2025, 3, 1))
    make_charge(unit2, date(2025, 3, 1))
    assert Charge.objects.count() == 2
