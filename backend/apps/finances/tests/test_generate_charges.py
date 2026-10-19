"""S3-A: generación mensual de cuotas ordinarias (idempotente)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.models import AuditLog
from apps.finances.models import Charge
from apps.finances.services import (
    FeeRateNotFoundError,
    InvalidPeriodError,
    generate_monthly_charges,
)

pytestmark = pytest.mark.django_db

MARZO = date(2025, 3, 1)


@pytest.fixture
def unidades(tower, make_unit):
    return [make_unit(tower, code) for code in ("101", "102", "103")]


def test_crea_una_cuota_por_unidad_activa(tower, unidades, fee_rate):
    result = generate_monthly_charges(MARZO)
    assert (result.created, result.existing, result.skipped) == (3, 0, 0)
    cargos = Charge.objects.all()
    assert cargos.count() == 3
    cargo = cargos.first()
    assert cargo.concept == "ORDINARY_FEE"
    assert cargo.amount == Decimal("80.00")
    assert cargo.period == MARZO
    assert cargo.due_date == date(2025, 3, 31)
    assert cargo.status == "OPEN"
    assert cargo.source == "SYSTEM"
    assert "03/2025" in cargo.description


def test_es_idempotente(tower, unidades, fee_rate):
    generate_monthly_charges(MARZO)
    segunda = generate_monthly_charges(MARZO)
    assert (segunda.created, segunda.existing) == (0, 3)
    assert Charge.objects.count() == 3


def test_completa_solo_las_unidades_que_faltan(tower, unidades, fee_rate, make_unit):
    generate_monthly_charges(MARZO)
    make_unit(tower, "104")
    resultado = generate_monthly_charges(MARZO)
    assert (resultado.created, resultado.existing) == (1, 3)


def test_normaliza_cualquier_fecha_al_primer_dia_del_mes(tower, unidades, fee_rate):
    generate_monthly_charges(date(2025, 3, 17))
    assert set(Charge.objects.values_list("period", flat=True)) == {MARZO}


@pytest.mark.parametrize(
    ("periodo", "vencimiento"),
    [
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2025, 2, 1), date(2025, 2, 28)),
        (date(2025, 4, 1), date(2025, 4, 30)),
        (date(2025, 12, 1), date(2025, 12, 31)),
    ],
)
def test_el_vencimiento_es_el_ultimo_dia_del_mes(
    tower, unidades, fee_rate, periodo, vencimiento
):
    generate_monthly_charges(periodo)
    assert set(Charge.objects.values_list("due_date", flat=True)) == {vencimiento}


def test_omite_unidades_inactivas(tower, make_unit, fee_rate):
    make_unit(tower, "101")
    make_unit(tower, "102", is_active=False)
    result = generate_monthly_charges(MARZO)
    assert result.created == 1
    assert result.skipped == 0


def test_omite_unidades_sin_inicio_de_cobro_o_con_inicio_posterior(
    tower, make_unit, fee_rate
):
    make_unit(tower, "101")
    make_unit(tower, "102", billing_start=None)
    make_unit(tower, "103", billing_start=date(2025, 4, 1))
    result = generate_monthly_charges(MARZO)
    assert (result.created, result.skipped) == (1, 2)


def test_cobra_el_mes_completo_si_el_inicio_cae_dentro_del_mes(
    tower, make_unit, fee_rate
):
    make_unit(tower, "101", billing_start=date(2025, 3, 20))
    assert generate_monthly_charges(MARZO).created == 1


def test_usa_la_tarifa_vigente_del_periodo(tower, unidades, make_fee_rate):
    make_fee_rate(
        tower, "80.00", valid_from=date(2020, 1, 1), valid_to=date(2025, 3, 31)
    )
    make_fee_rate(tower, "90.00", valid_from=date(2025, 4, 1))
    generate_monthly_charges(MARZO)
    generate_monthly_charges(date(2025, 4, 1))
    assert set(
        Charge.objects.filter(period=MARZO).values_list("amount", flat=True)
    ) == {Decimal("80.00")}
    assert set(
        Charge.objects.filter(period=date(2025, 4, 1)).values_list("amount", flat=True)
    ) == {Decimal("90.00")}


def test_cambiar_la_tarifa_no_altera_los_cargos_ya_emitidos(
    tower, unidades, fee_rate, make_fee_rate
):
    generate_monthly_charges(MARZO)
    make_fee_rate(tower, "120.00", valid_from=date(2025, 3, 1))
    generate_monthly_charges(MARZO)
    assert set(Charge.objects.values_list("amount", flat=True)) == {Decimal("80.00")}


def test_con_varias_tarifas_vigentes_gana_la_de_inicio_mas_reciente(
    tower, unidades, make_fee_rate
):
    make_fee_rate(tower, "80.00", valid_from=date(2020, 1, 1))
    make_fee_rate(tower, "85.00", valid_from=date(2025, 1, 1))
    generate_monthly_charges(MARZO)
    assert set(Charge.objects.values_list("amount", flat=True)) == {Decimal("85.00")}


def test_sin_tarifa_vigente_falla_y_no_crea_nada(tower, unidades, make_fee_rate):
    make_fee_rate(
        tower, "80.00", valid_from=date(2020, 1, 1), valid_to=date(2024, 12, 31)
    )
    with pytest.raises(FeeRateNotFoundError):
        generate_monthly_charges(MARZO)
    assert Charge.objects.count() == 0


def test_sin_ninguna_tarifa_falla(tower, unidades):
    with pytest.raises(FeeRateNotFoundError):
        generate_monthly_charges(MARZO)


def test_no_permite_generar_un_periodo_futuro(tower, unidades, fee_rate):
    hoy = timezone.localdate()
    siguiente = (hoy.replace(day=1) + timedelta(days=32)).replace(day=1)
    with pytest.raises(InvalidPeriodError):
        generate_monthly_charges(siguiente)
    assert Charge.objects.count() == 0


def test_permite_generar_el_mes_actual(tower, make_unit, fee_rate):
    make_unit(tower, "101")
    actual = timezone.localdate().replace(day=1)
    assert generate_monthly_charges(actual).created == 1


def test_el_periodo_debe_ser_una_fecha(tower, unidades, fee_rate):
    with pytest.raises(InvalidPeriodError):
        generate_monthly_charges("2025-03")


def test_limita_a_la_torre_indicada(tower, other_tower, make_unit, make_fee_rate):
    make_unit(tower, "101")
    make_unit(other_tower, "101")
    make_fee_rate(tower)
    make_fee_rate(other_tower, "70.00")
    generate_monthly_charges(MARZO, tower=tower)
    assert Charge.objects.count() == 1
    assert Charge.objects.get().unit.tower == tower


def test_sin_torre_procesa_todas_con_su_propia_tarifa(
    tower, other_tower, make_unit, make_fee_rate
):
    make_unit(tower, "101")
    make_unit(other_tower, "101")
    make_fee_rate(tower)
    make_fee_rate(other_tower, "70.00")
    result = generate_monthly_charges(MARZO)
    assert result.created == 2
    assert Charge.objects.get(unit__tower=other_tower).amount == Decimal("70.00")


def test_si_una_torre_no_tiene_tarifa_no_se_crea_nada(
    tower, other_tower, make_unit, make_fee_rate
):
    make_unit(tower, "101")
    make_unit(other_tower, "101")
    make_fee_rate(tower)
    with pytest.raises(FeeRateNotFoundError):
        generate_monthly_charges(MARZO)
    assert Charge.objects.count() == 0


def test_audita_cada_generacion_sin_datos_personales(tower, unidades, fee_rate, admin):
    generate_monthly_charges(MARZO, tower=tower, actor=admin, ip="10.0.0.5")
    evento = AuditLog.objects.get(action="CHARGES_GENERATED")
    assert evento.entity_type == "Tower"
    assert evento.entity_id == str(tower.pk)
    assert evento.actor_label == admin.email
    assert evento.ip_address == "10.0.0.5"
    assert evento.changes == {
        "period": "2025-03-01",
        "created": 3,
        "existing": 0,
        "skipped": 0,
        "amount": "80.00",
    }


def test_una_segunda_ejecucion_tambien_queda_auditada(tower, unidades, fee_rate):
    generate_monthly_charges(MARZO, tower=tower)
    generate_monthly_charges(MARZO, tower=tower)
    creados = list(
        AuditLog.objects.filter(action="CHARGES_GENERATED")
        .order_by("created_at")
        .values_list("changes__created", flat=True)
    )
    assert creados == [3, 0]


def test_el_actor_puede_ser_una_etiqueta_del_sistema(tower, unidades, fee_rate):
    generate_monthly_charges(MARZO, tower=tower, actor_label="comando generate_charges")
    evento = AuditLog.objects.get(action="CHARGES_GENERATED")
    assert evento.actor_id == ""
    assert evento.actor_label == "comando generate_charges"
