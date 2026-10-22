"""Restricciones en la base de datos de Payment, PaymentVoucher y PaymentAllocation."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.finances import models

pytestmark = pytest.mark.django_db


def test_el_estado_por_defecto_es_pendiente(unit):
    pago = models.Payment.objects.create(
        unit=unit,
        amount=Decimal("80.00"),
        method="TRANSFER",
        operation_number="A1",
        paid_at="2025-03-01",
        source="RESIDENT",
    )
    assert pago.status == "PENDING"
    assert pago.reviewed_by is None
    assert pago.reviewed_at is None
    assert pago.rejection_reason == ""


def test_no_existe_el_metodo_efectivo():
    assert set(models.Payment.Method.values) == {
        "TRANSFER",
        "DEPOSIT",
        "YAPE",
        "PLIN",
        "LEGACY",
    }


def test_estados_y_origenes_del_pago():
    assert set(models.Payment.Status.values) == {
        "PENDING",
        "APPROVED",
        "REJECTED",
        "VOID",
    }
    assert set(models.Payment.Source.values) == {"RESIDENT", "STAFF", "IMPORT"}


@pytest.mark.parametrize("monto", ["0.00", "-5.00"])
def test_el_monto_del_pago_debe_ser_positivo_en_la_bd(unit, make_payment, monto):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_payment(unit, monto)


def test_el_numero_de_operacion_es_obligatorio_salvo_legacy(unit, make_payment):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_payment(unit, operation_number="")
    legacy = make_payment(unit, method="LEGACY", operation_number="")
    assert legacy.pk is not None


def test_un_pago_rechazado_exige_motivo(unit, make_payment):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_payment(unit, status="REJECTED", rejection_reason="")


def test_un_pago_aprobado_exige_fecha_de_revision(unit, make_payment):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_payment(unit, status="APPROVED", reviewed_at=None)


def test_no_se_borra_una_unidad_con_pagos(unit, make_payment):
    make_payment(unit)
    with pytest.raises(ProtectedError):
        unit.delete()


def test_borrar_al_usuario_deja_el_pago_sin_remitente(unit, make_payment, make_user):
    usuario = make_user("remitente@valleblanco.pe")
    pago = make_payment(unit, submitted_by=usuario, reviewed_by=usuario)
    usuario.delete()
    pago.refresh_from_db()
    assert pago.submitted_by is None
    assert pago.reviewed_by is None


def test_la_asignacion_debe_ser_positiva(unit, make_payment, make_charge):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, status="APPROVED")
    with pytest.raises(IntegrityError), transaction.atomic():
        models.PaymentAllocation.objects.create(
            payment=pago, charge=cargo, amount=Decimal("0.00")
        )


def test_una_asignacion_por_pago_y_cargo(unit, make_payment, make_charge):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, status="APPROVED")
    models.PaymentAllocation.objects.create(
        payment=pago, charge=cargo, amount=Decimal("10.00")
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        models.PaymentAllocation.objects.create(
            payment=pago, charge=cargo, amount=Decimal("20.00")
        )


def test_no_se_borra_un_pago_con_asignaciones_ni_comprobantes(
    unit, make_payment, make_charge
):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, status="APPROVED")
    models.PaymentAllocation.objects.create(
        payment=pago, charge=cargo, amount=Decimal("10.00")
    )
    with pytest.raises(ProtectedError):
        pago.delete()
    otro = make_payment(unit)
    models.PaymentVoucher.objects.create(
        payment=otro, file="x.jpg", sha256="a" * 64, content_type="image/jpeg", size=10
    )
    with pytest.raises(ProtectedError):
        otro.delete()
    with pytest.raises(ProtectedError):
        cargo.delete()


@pytest.mark.parametrize("tamano", [0, 5 * 1024 * 1024 + 1])
def test_el_tamano_del_comprobante_se_valida_en_la_bd(unit, make_payment, tamano):
    pago = make_payment(unit)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.PaymentVoucher.objects.create(
            payment=pago,
            file="x.jpg",
            sha256="a" * 64,
            content_type="image/jpeg",
            size=tamano,
        )
