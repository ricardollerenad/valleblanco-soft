"""S2: comando que crea la torre y sus 80 unidades."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.properties.models import Condominium, Tower, Unit

pytestmark = pytest.mark.django_db


def test_crea_la_torre_2_con_80_unidades():
    call_command("seed_tower")

    tower = Tower.objects.get(name="Torre 2")
    assert tower.floors == 10
    assert tower.condominium.name == "ValleBlanco"
    assert Unit.objects.filter(tower=tower).count() == 80


def test_los_codigos_son_piso_mas_numero_de_dos_cifras():
    call_command("seed_tower")

    codes = set(Unit.objects.values_list("code", flat=True))
    assert {"101", "108", "201", "901", "1001", "1008"} <= codes
    unit = Unit.objects.get(code="1008")
    assert (unit.floor, unit.number) == (10, 8)


def test_se_puede_repetir_sin_duplicar():
    call_command("seed_tower")
    call_command("seed_tower")

    assert Condominium.objects.count() == 1
    assert Tower.objects.count() == 1
    assert Unit.objects.count() == 80


def test_completa_las_unidades_que_falten():
    call_command("seed_tower")
    Unit.objects.filter(code="505").delete()

    call_command("seed_tower")

    assert Unit.objects.count() == 80


def test_acepta_otros_valores():
    call_command(
        "seed_tower",
        "--condominium",
        "Otro",
        "--tower",
        "Torre 3",
        "--floors",
        "2",
        "--per-floor",
        "3",
    )

    tower = Tower.objects.get(name="Torre 3")
    assert tower.condominium.name == "Otro"
    assert sorted(Unit.objects.filter(tower=tower).values_list("code", flat=True)) == [
        "101",
        "102",
        "103",
        "201",
        "202",
        "203",
    ]


@pytest.mark.parametrize("args", [["--floors", "0"], ["--per-floor", "0"]])
def test_valores_invalidos_fallan_sin_crear_nada(args):
    with pytest.raises(CommandError):
        call_command("seed_tower", *args)
    assert Unit.objects.count() == 0
    assert Tower.objects.count() == 0
