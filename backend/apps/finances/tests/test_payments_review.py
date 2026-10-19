"""reject_payment y void_payment."""

import json
from datetime import date
from decimal import Decimal

import pytest

from apps.core.models import AuditLog
from apps.finances import models, services

pytestmark = pytest.mark.django_db

D = Decimal


# --- Rechazo -----------------------------------------------------------------


def test_rechaza_un_pago_pendiente_con_motivo(unit, admin, make_payment):
    pago = make_payment(unit)
    services.reject_payment(pago, "  Monto distinto al del banco ", actor=admin)
    pago.refresh_from_db()
    assert pago.status == "REJECTED"
    assert pago.rejection_reason == "Monto distinto al del banco"
    assert pago.reviewed_by == admin
    assert pago.reviewed_at is not None


def test_rechazar_actualiza_el_objeto_recibido(unit, make_payment):
    pago = make_payment(unit)
    services.reject_payment(pago, "Ilegible")
    assert pago.status == "REJECTED"
    assert pago.rejection_reason == "Ilegible"


@pytest.mark.parametrize("motivo", ["", "   ", None])
def test_el_rechazo_exige_motivo(unit, make_payment, motivo):
    pago = make_payment(unit)
    with pytest.raises(services.InvalidPaymentError):
        services.reject_payment(pago, motivo)
    pago.refresh_from_db()
    assert pago.status == "PENDING"


def test_el_motivo_se_recorta_a_255_caracteres(unit, make_payment):
    pago = make_payment(unit)
    services.reject_payment(pago, "x" * 400)
    pago.refresh_from_db()
    assert len(pago.rejection_reason) == 255


@pytest.mark.parametrize("estado", ["APPROVED", "REJECTED", "VOID"])
def test_solo_se_rechaza_un_pago_pendiente(unit, make_payment, estado):
    pago = make_payment(unit, status=estado)
    with pytest.raises(services.PaymentNotPendingError):
        services.reject_payment(pago, "Motivo")


def test_rechazar_no_aplica_nada_y_libera_el_duplicado(unit, make_charge, make_payment):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, "80.00", operation_number="OP123")
    services.reject_payment(pago, "Ilegible")
    assert models.PaymentAllocation.objects.count() == 0
    assert services.pending_amount(cargo) == D("80.00")
    nuevo = services.submit_payment(
        unit, "80.00", "TRANSFER", "OP123", date(2025, 3, 1)
    )
    assert nuevo.status == "PENDING"


def test_audita_el_rechazo(unit, admin, make_payment):
    pago = make_payment(unit, operation_number="0012345678")
    services.reject_payment(pago, "Comprobante ilegible", actor=admin, ip="10.0.0.5")
    evento = AuditLog.objects.get(action="PAYMENT_REJECTED")
    assert evento.entity_type == "Payment"
    assert evento.entity_id == str(pago.pk)
    assert evento.actor_id == str(admin.pk)
    assert evento.changes["reason"] == "Comprobante ilegible"
    assert "0012345678" not in json.dumps(evento.changes)


# --- Anulación ---------------------------------------------------------------


def test_anula_un_pago_aprobado(unit, admin, make_charge, pay):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = pay(unit, "80.00")
    assert services.pending_amount(cargo) == D("0.00")
    services.void_payment(pago, "Depósito devuelto por el banco", actor=admin)
    pago.refresh_from_db()
    assert pago.status == "VOID"
    assert services.pending_amount(cargo) == D("80.00")


def test_anular_actualiza_el_objeto_recibido(unit, pay):
    pago = pay(unit, "80.00")
    services.void_payment(pago, "Error de registro")
    assert pago.status == "VOID"


def test_anular_conserva_las_asignaciones_pero_dejan_de_contar(unit, make_charge, pay):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = pay(unit, "80.00")
    services.void_payment(pago, "Error de registro")
    assert models.PaymentAllocation.objects.filter(payment=pago).count() == 1
    assert services.allocated_totals([cargo.pk]) == {}
    assert services.pending_balance(unit) == D("80.00")


def test_anular_recalcula_el_saldo_a_favor(unit, make_charge, pay):
    make_charge(unit, date(2025, 1, 1))
    grande = pay(unit, "200.00")
    pay(unit, "30.00")
    assert services.credit_balance(unit) == D("150.00")
    services.void_payment(grande, "Error de registro")
    assert services.credit_balance(unit) == D("30.00")


def test_un_cargo_con_pagos_aplicados_no_se_anula_hasta_anular_el_pago(
    unit, make_charge, pay
):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = pay(unit, "80.00")
    with pytest.raises(services.ChargeHasAllocationsError):
        services.void_charge(cargo, "Cargo duplicado")
    services.void_payment(pago, "Error de registro")
    services.void_charge(cargo, "Cargo duplicado")
    cargo.refresh_from_db()
    assert cargo.status == "VOID"


def test_un_pago_pendiente_no_se_anula(unit, make_payment):
    pago = make_payment(unit)
    with pytest.raises(services.PaymentNotApprovedError):
        services.void_payment(pago, "Motivo")
    pago.refresh_from_db()
    assert pago.status == "PENDING"


@pytest.mark.parametrize("estado", ["REJECTED", "VOID"])
def test_solo_se_anula_un_pago_aprobado(unit, make_payment, estado):
    pago = make_payment(unit, status=estado)
    with pytest.raises(services.PaymentNotApprovedError):
        services.void_payment(pago, "Motivo")


def test_anular_dos_veces_falla(unit, pay):
    pago = pay(unit, "80.00")
    services.void_payment(pago, "Error de registro")
    with pytest.raises(services.PaymentNotApprovedError):
        services.void_payment(pago, "Otra vez")
    assert AuditLog.objects.filter(action="PAYMENT_VOIDED").count() == 1


@pytest.mark.parametrize("motivo", ["", "   ", None])
def test_la_anulacion_exige_motivo(unit, pay, motivo):
    pago = pay(unit, "80.00")
    with pytest.raises(services.InvalidPaymentError):
        services.void_payment(pago, motivo)
    pago.refresh_from_db()
    assert pago.status == "APPROVED"


def test_un_pago_anulado_no_vuelve_a_aprobarse(unit, make_charge, pay):
    make_charge(unit, date(2025, 1, 1))
    pago = pay(unit, "80.00")
    services.void_payment(pago, "Error de registro")
    with pytest.raises(services.PaymentNotPendingError):
        services.approve_payment(pago)


def test_un_pago_anulado_libera_el_duplicado(unit, pay):
    pago = pay(unit, "80.00", operation_number="OP123")
    services.void_payment(pago, "Error de registro")
    nuevo = services.submit_payment(
        unit, "80.00", "TRANSFER", "OP123", date(2025, 3, 1)
    )
    assert nuevo.status == "PENDING"


def test_audita_la_anulacion_con_lo_liberado(unit, admin, make_charge, pay):
    make_charge(unit, date(2025, 1, 1))
    pago = pay(unit, "200.00", operation_number="0012345678")
    services.void_payment(pago, "Error de registro", actor=admin, ip="10.0.0.5")
    evento = AuditLog.objects.get(action="PAYMENT_VOIDED")
    assert evento.entity_type == "Payment"
    assert evento.entity_id == str(pago.pk)
    assert evento.actor_id == str(admin.pk)
    assert evento.changes["reason"] == "Error de registro"
    assert evento.changes["amount"] == "200.00"
    assert evento.changes["released"] == "80.00"
    assert "0012345678" not in json.dumps(evento.changes)
