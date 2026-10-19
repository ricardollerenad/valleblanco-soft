"""S3-A: comando de gestión `generate_charges` (cron el día 1)."""

from datetime import date
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.core.models import AuditLog
from apps.finances.models import Charge

pytestmark = pytest.mark.django_db


def run(*args):
    out = StringIO()
    call_command("generate_charges", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


@pytest.fixture
def listo(tower, make_unit, fee_rate):
    for code in ("101", "102"):
        make_unit(tower, code)
    return tower


def test_genera_las_cuotas_del_periodo(listo):
    salida = run("--period", "2025-03")
    assert Charge.objects.filter(period=date(2025, 3, 1)).count() == 2
    assert "2 cargos nuevos" in salida


def test_repetir_el_comando_no_duplica(listo):
    run("--period", "2025-03")
    salida = run("--period", "2025-03")
    assert Charge.objects.count() == 2
    assert "0 cargos nuevos" in salida
    assert "2 ya existían" in salida


def test_sin_periodo_usa_el_mes_actual(listo):
    run()
    actual = timezone.localdate().replace(day=1)
    assert set(Charge.objects.values_list("period", flat=True)) == {actual}


@pytest.mark.parametrize("periodo", ["2025-13", "2025-00", "marzo", "2025-3", "2025"])
def test_rechaza_periodos_mal_escritos(listo, periodo):
    with pytest.raises(CommandError):
        run("--period", periodo)
    assert Charge.objects.count() == 0


def test_sin_tarifa_vigente_informa_el_error(tower, make_unit):
    make_unit(tower, "101")
    with pytest.raises(CommandError, match="tarifa"):
        run("--period", "2025-03")


def test_filtra_por_nombre_de_torre(listo, other_tower, make_unit, make_fee_rate):
    make_unit(other_tower, "101")
    make_fee_rate(other_tower, "70.00")
    run("--period", "2025-03", "--tower", "Torre 3")
    assert Charge.objects.count() == 1
    assert Charge.objects.get().unit.tower == other_tower


def test_torre_inexistente(listo):
    with pytest.raises(CommandError, match="Torre"):
        run("--period", "2025-03", "--tower", "Torre 99")


def test_avisa_de_las_unidades_sin_inicio_de_cobro(listo, make_unit):
    make_unit(listo, "103", billing_start=None)
    out, err = StringIO(), StringIO()
    call_command("generate_charges", "--period", "2025-03", stdout=out, stderr=err)
    assert "1 unidades sin inicio de cobro" in err.getvalue()


def test_la_auditoria_registra_que_lo_ejecuto_el_comando(listo):
    run("--period", "2025-03")
    evento = AuditLog.objects.get(action="CHARGES_GENERATED")
    assert evento.actor_label == "comando generate_charges"
