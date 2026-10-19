"""Fixtures compartidas de las pruebas de finanzas."""

import itertools
import uuid
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


# --- Pagos y comprobantes (S4-A) ---------------------------------------------


@pytest.fixture(autouse=True)
def voucher_root(settings, tmp_path):
    """Los comprobantes de las pruebas van a un directorio temporal, nunca al real."""
    settings.VOUCHER_ROOT = tmp_path / "vouchers"
    return settings.VOUCHER_ROOT


@pytest.fixture
def make_payment():
    """Crea un pago directo en la BD (sin pasar por los servicios)."""
    from django.utils import timezone

    from apps.finances import models

    contador = itertools.count(1)

    def _make(unit, amount="80.00", **extra):
        extra.setdefault("method", "TRANSFER")
        extra.setdefault("operation_number", f"OP{next(contador)}")
        extra.setdefault("paid_at", date(2025, 3, 1))
        extra.setdefault("status", "PENDING")
        extra.setdefault("source", "RESIDENT")
        if extra["status"] == "APPROVED":
            extra.setdefault("reviewed_at", timezone.now())
        if extra["status"] == "REJECTED":
            extra.setdefault("rejection_reason", "Motivo de prueba")
        return models.Payment.objects.create(unit=unit, amount=Decimal(amount), **extra)

    return _make


@pytest.fixture
def pay(make_payment, admin):
    """Crea un pago y, por defecto, lo aprueba con los servicios reales."""
    from apps.finances import services

    def _pay(unit, amount="80.00", *, approve=True, **extra):
        payment = make_payment(unit, amount, **extra)
        if approve:
            services.approve_payment(payment, actor=admin)
        return payment

    return _pay


@pytest.fixture
def voucher_file():
    """Bytes con la firma real de cada tipo; `salt` distingue el contenido."""
    cabeceras = {
        "jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00",
        "png": b"\x89PNG\r\n\x1a\n",
        "webp": b"RIFF\x24\x00\x00\x00WEBPVP8 ",
        "pdf": b"%PDF-1.7\n",
    }

    def _make(kind="jpg", salt=None, size=None):
        cuerpo = cabeceras[kind] + (uuid.uuid4().bytes if salt is None else salt)
        if size is not None:
            cuerpo += b"\x00" * (size - len(cuerpo))
        return cuerpo

    return _make
