"""submit_payment: registro, normalización, duplicados y auditoría."""

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.models import AuditLog
from apps.finances import models, services

pytestmark = pytest.mark.django_db

PAID_AT = date(2025, 3, 1)


def enviar(unit, amount="80.00", method="TRANSFER", operation="0012345678", **extra):
    paid_at = extra.pop("paid_at", PAID_AT)
    return services.submit_payment(unit, amount, method, operation, paid_at, **extra)


def test_registra_un_pago_pendiente(unit, admin):
    pago = enviar(unit, submitted_by=admin)
    assert pago.pk is not None
    assert pago.status == "PENDING"
    assert pago.amount == Decimal("80.00")
    assert pago.method == "TRANSFER"
    assert pago.operation_number == "0012345678"
    assert pago.paid_at == PAID_AT
    assert pago.source == "RESIDENT"
    assert pago.submitted_by == admin
    assert pago.reviewed_by is None
    assert models.Payment.objects.count() == 1


def test_el_remitente_por_defecto_es_el_actor(unit, admin):
    assert enviar(unit, actor=admin).submitted_by == admin


def test_el_origen_personal_de_administracion(unit):
    assert enviar(unit, source="STAFF").source == "STAFF"


def test_normaliza_el_numero_de_operacion(unit):
    pago = enviar(unit, operation="  op-00 12\u00a0 ")
    assert pago.operation_number == "OP0012"


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [("80.5", "80.50"), (Decimal("12"), "12.00"), (80, "80.00")],
)
def test_acepta_montos_como_texto_decimal_o_entero(unit, entrada, esperado):
    assert enviar(unit, amount=entrada).amount == Decimal(esperado)


@pytest.mark.parametrize(
    "monto", [80.0, True, "0", "-1", "abc", "80.123", None, "NaN", "Infinity"]
)
def test_rechaza_montos_invalidos(unit, monto):
    with pytest.raises(services.InvalidPaymentError):
        enviar(unit, amount=monto)
    assert models.Payment.objects.count() == 0


@pytest.mark.parametrize("metodo", ["CASH", "", "legacy", "EFECTIVO", None])
def test_rechaza_metodos_invalidos(unit, metodo):
    with pytest.raises(services.InvalidPaymentError):
        enviar(unit, method=metodo)


def test_legacy_solo_con_origen_importacion(unit):
    with pytest.raises(services.InvalidPaymentError):
        enviar(unit, method="LEGACY", operation="")
    pago = enviar(unit, method="LEGACY", operation="", source="IMPORT")
    assert pago.method == "LEGACY"
    assert pago.operation_number == ""


def test_rechaza_un_origen_desconocido(unit):
    with pytest.raises(services.InvalidPaymentError):
        enviar(unit, source="BANK")


def test_rechaza_fechas_futuras_y_acepta_hoy(unit):
    hoy = timezone.localdate()
    with pytest.raises(services.InvalidPaymentError):
        enviar(unit, paid_at=hoy + timedelta(days=1))
    assert enviar(unit, paid_at=hoy).paid_at == hoy


@pytest.mark.parametrize("fecha", ["2025-03-01", None, timezone.now()])
def test_la_fecha_debe_ser_una_fecha(unit, fecha):
    with pytest.raises(services.InvalidPaymentError):
        enviar(unit, paid_at=fecha)


@pytest.mark.parametrize("numero", ["", "   ", "---", None])
def test_el_numero_de_operacion_es_obligatorio(unit, numero):
    with pytest.raises(services.InvalidPaymentError):
        enviar(unit, operation=numero)


def test_rechaza_un_numero_de_operacion_demasiado_largo(unit):
    with pytest.raises(services.InvalidPaymentError):
        enviar(unit, operation="9" * 51)


def test_rechaza_unidades_inactivas(unit):
    unit.is_active = False
    unit.save()
    with pytest.raises(services.InvalidPaymentError):
        enviar(unit)


# --- Duplicados --------------------------------------------------------------


def test_un_duplicado_pendiente_se_rechaza(unit):
    primero = enviar(unit, operation="OP-123")
    with pytest.raises(services.DuplicatePaymentError) as error:
        enviar(unit, operation="op 123")
    assert error.value.existing_payment_id == primero.pk
    assert models.Payment.objects.count() == 1


def test_un_duplicado_aprobado_se_rechaza(unit, pay):
    pay(unit, "80.00", operation_number="OP123")
    with pytest.raises(services.DuplicatePaymentError):
        enviar(unit, operation="OP123")


def test_el_duplicado_se_detecta_entre_unidades_de_la_misma_torre(unit, unit2):
    enviar(unit, operation="OP123")
    with pytest.raises(services.DuplicatePaymentError):
        enviar(unit2, operation="OP123")


def test_la_misma_operacion_en_otra_torre_no_es_duplicado(unit, other_tower, make_unit):
    enviar(unit, operation="OP123")
    ajena = make_unit(other_tower, "101")
    assert enviar(ajena, operation="OP123").pk is not None


def test_cambiar_monto_o_metodo_no_es_duplicado(unit):
    enviar(unit, operation="OP123")
    assert enviar(unit, amount="81.00", operation="OP123").pk is not None
    assert enviar(unit, method="YAPE", operation="OP123").pk is not None


@pytest.mark.parametrize("estado", ["REJECTED", "VOID"])
def test_un_pago_rechazado_o_anulado_no_bloquea(unit, make_payment, estado):
    make_payment(unit, "80.00", operation_number="OP123", status=estado)
    assert enviar(unit, operation="OP123").status == "PENDING"


def test_los_pagos_legacy_sin_numero_no_se_comparan(unit):
    enviar(unit, method="LEGACY", operation="", source="IMPORT")
    otro = enviar(unit, method="LEGACY", operation="", source="IMPORT")
    assert otro.pk is not None


# --- Auditoría ---------------------------------------------------------------


def test_audita_el_registro_sin_el_numero_completo(unit, admin):
    pago = enviar(unit, operation="0012345678", actor=admin, ip="10.0.0.5")
    evento = AuditLog.objects.get(action="PAYMENT_SUBMITTED")
    assert evento.entity_type == "Payment"
    assert evento.entity_id == str(pago.pk)
    assert evento.actor_id == str(admin.pk)
    assert evento.ip_address == "10.0.0.5"
    assert evento.changes["operation_last4"] == "5678"
    assert evento.changes["amount"] == "80.00"
    assert evento.changes["method"] == "TRANSFER"
    assert "0012345678" not in json.dumps(evento.changes)


def test_un_rechazo_por_duplicado_no_deja_auditoria_extra(unit):
    enviar(unit, operation="OP123")
    with pytest.raises(services.DuplicatePaymentError):
        enviar(unit, operation="OP123")
    assert AuditLog.objects.filter(action="PAYMENT_SUBMITTED").count() == 1
