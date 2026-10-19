"""Fixtures compartidas de las pruebas de propiedades."""

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

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


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def tower():
    condominium = Condominium.objects.create(name="ValleBlanco")
    return Tower.objects.create(condominium=condominium, name="Torre 2", floors=10)


@pytest.fixture
def unit(tower):
    return Unit.objects.create(tower=tower, code="101", floor=1, number=1)


@pytest.fixture
def unit2(tower):
    return Unit.objects.create(tower=tower, code="102", floor=1, number=2)


@pytest.fixture
def admin():
    return User.objects.create_superuser("admin@valleblanco.pe", PASSWORD)


@pytest.fixture
def admin_client(admin):
    return client_for(admin)


@pytest.fixture
def make_user():
    def _make(email):
        return User.objects.create_user(email, PASSWORD)

    return _make


@pytest.fixture
def make_person():
    def _make(name="Ana Torres", user=None, **extra):
        return Person.objects.create(full_name=name, user=user, **extra)

    return _make


@pytest.fixture
def make_occupancy():
    def _make(unit, person, relation="OWNER", is_primary=False, **extra):
        extra.setdefault("start_date", date(2024, 1, 1))
        return UnitOccupancy.objects.create(
            unit=unit, person=person, relation=relation, is_primary=is_primary, **extra
        )

    return _make


@pytest.fixture
def give_role(tower):
    def _give(user, role):
        return RoleAssignment.objects.create(user=user, role=role, tower=tower)

    return _give


@pytest.fixture
def owner(make_user, make_person, make_occupancy, unit):
    """Propietario con usuario y la unidad 101 como titular principal."""
    user = make_user("duenio@valleblanco.pe")
    person = make_person("Dueño Uno", user=user)
    make_occupancy(unit, person, "OWNER", is_primary=True)
    return user
