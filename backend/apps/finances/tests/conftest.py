"""Fixtures compartidas de las pruebas de finanzas."""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.finances.models import FeeRate
from apps.properties.models import Condominium, Tower, Unit

User = get_user_model()
PASSWORD = "Cielo-Azul-9347-Zq"


@pytest.fixture
def tower():
    condominium = Condominium.objects.create(name="ValleBlanco")
    return Tower.objects.create(condominium=condominium, name="Torre 2", floors=10)


@pytest.fixture
def other_tower(tower):
    return Tower.objects.create(
        condominium=tower.condominium, name="Torre 3", floors=10
    )


@pytest.fixture
def make_unit():
    def _make(tower, code, billing_start=date(2024, 1, 1), **extra):
        floor, number = int(code[:-2]), int(code[-2:])
        return Unit.objects.create(
            tower=tower,
            code=code,
            floor=floor,
            number=number,
            billing_start=billing_start,
            **extra,
        )

    return _make


@pytest.fixture
def unit(tower, make_unit):
    return make_unit(tower, "101")


@pytest.fixture
def unit2(tower, make_unit):
    return make_unit(tower, "102")


@pytest.fixture
def make_fee_rate():
    def _make(tower, amount="80.00", valid_from=date(2020, 1, 1), **extra):
        extra.setdefault("concept", "ORDINARY_FEE")
        return FeeRate.objects.create(
            tower=tower, amount=Decimal(amount), valid_from=valid_from, **extra
        )

    return _make


@pytest.fixture
def fee_rate(tower, make_fee_rate):
    return make_fee_rate(tower)


@pytest.fixture
def admin():
    return User.objects.create_superuser("admin@valleblanco.pe", PASSWORD)


@pytest.fixture
def make_charge():
    from apps.finances.models import Charge

    def _make(unit, period, amount="80.00", due_date=None, **extra):
        extra.setdefault("concept", "ORDINARY_FEE")
        extra.setdefault("status", "OPEN")
        extra.setdefault("source", "SYSTEM")
        if due_date is None:
            due_date = date(period.year, period.month, 28)
        return Charge.objects.create(
            unit=unit,
            period=period,
            amount=Decimal(amount),
            due_date=due_date,
            **extra,
        )

    return _make
