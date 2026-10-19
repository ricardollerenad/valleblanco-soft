"""Fixtures compartidas de las pruebas de finanzas."""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.finances.models import FeeRate
from apps.properties.models import (
    Condominium,
    Person,
    RoleAssignment,
    Tower,
    Unit,
    UnitOccupancy,
)

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


# --- Personas y roles (S3-B) --------------------------------------------------


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def admin_client(admin):
    return client_for(admin)


@pytest.fixture
def make_user():
    def _make(email):
        return User.objects.create_user(email, PASSWORD)

    return _make


@pytest.fixture
def give_role(tower):
    def _give(user, role):
        return RoleAssignment.objects.create(user=user, role=role, tower=tower)

    return _give


@pytest.fixture
def make_resident(make_user):
    """Crea un usuario con persona y una ocupación vigente en la unidad."""

    def _make(email, unit, relation="OWNER", is_primary=False, **extra):
        user = make_user(email)
        person = Person.objects.create(full_name=email.split("@")[0], user=user)
        extra.setdefault("start_date", date(2024, 1, 1))
        UnitOccupancy.objects.create(
            unit=unit,
            person=person,
            relation=relation,
            is_primary=is_primary,
            **extra,
        )
        return user

    return _make


@pytest.fixture
def actores(tower, unit, unit2, admin, make_user, give_role, make_resident):
    """Un cliente por rol: la unidad 101 es de `propietario` e `inquilino`;
    `vecino` es propietario de la 102."""
    admin_rol = make_user("admin-rol@valleblanco.pe")
    give_role(admin_rol, "ADMIN")
    junta = make_user("junta@valleblanco.pe")
    give_role(junta, "BOARD")
    tesorero = make_user("tesorero@valleblanco.pe")
    give_role(tesorero, "TREASURER")
    return {
        "anonimo": APIClient(),
        "admin": client_for(admin),
        "admin_rol": client_for(admin_rol),
        "junta": client_for(junta),
        "tesorero": client_for(tesorero),
        "propietario": client_for(
            make_resident("propietario@valleblanco.pe", unit, "OWNER", True)
        ),
        "inquilino": client_for(
            make_resident("inquilino@valleblanco.pe", unit, "TENANT")
        ),
        "vecino": client_for(
            make_resident("vecino@valleblanco.pe", unit2, "OWNER", True)
        ),
    }
