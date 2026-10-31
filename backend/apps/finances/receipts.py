"""Emisión del recibo interno de un pago aprobado.

`issue_receipt` se llama desde `services.approve_payment`, dentro de su transacción:
el recibo nace (o no nace) junto con la aplicación a cargos. Este módulo no importa
`services` (sería un ciclo).

Correlativo `R-AAAA-NNNNNN`: una fila de `ReceiptCounter` por año. Se bloquea con
`select_for_update` hasta el final de la transacción, así dos aprobaciones
simultáneas nunca obtienen el mismo número y, si la transacción se revierte, el
número se libera (sin huecos). Como el bloqueo del contador es el último que toma
`approve_payment` (después de unidad, pago y cargos), no hay interbloqueos.
"""

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.audit import log_event
from apps.properties.models import Unit, UnitOccupancy

from .models import Concept, Payment, PaymentAllocation, Receipt, ReceiptCounter

SNAPSHOT_VERSION = 1
MASK = "****"


def _ahora():
    """Punto único del reloj de emisión (las pruebas lo sustituyen)."""
    return timezone.now()


def mask_operation(operation_number):
    """`****` más los 4 últimos caracteres: el recibo nunca trae el número completo."""
    return MASK + (operation_number or "")[-4:]


def _money(value):
    return f"{Decimal(value):.2f}"


def next_number(year):
    """Siguiente correlativo del año. Exige una transacción abierta."""
    ReceiptCounter.objects.bulk_create(
        [ReceiptCounter(year=year)], ignore_conflicts=True
    )
    counter = ReceiptCounter.objects.select_for_update().get(year=year)
    counter.last_number += 1
    counter.save(update_fields=["last_number", "updated_at"])
    return f"R-{year}-{counter.last_number:06d}"


def _primary_holder(unit_id):
    """Titular principal vigente (el que recibe el recibo), o `None`."""
    occupancy = (
        UnitOccupancy.objects.select_related("person")
        .filter(unit_id=unit_id, is_primary=True, end_date__isnull=True)
        .first()
    )
    if occupancy is None:
        return None
    return {"name": occupancy.person.full_name}


def _periods(allocations):
    """Periodos que cubre el pago, en el orden en que se aplicó."""
    charge_ids = [allocation.charge_id for allocation in allocations]
    totals = {
        row["charge_id"]: row["total"]
        for row in PaymentAllocation.objects.filter(
            charge_id__in=charge_ids, payment__status=Payment.Status.APPROVED
        )
        .values("charge_id")
        .annotate(total=Sum("amount"))
    }
    return [
        {
            "period": allocation.charge.period.isoformat(),
            "concept": allocation.charge.concept,
            "concept_label": Concept(allocation.charge.concept).label,
            "description": allocation.charge.description,
            "applied": _money(allocation.amount),
            "covered": totals.get(allocation.charge_id, Decimal("0"))
            >= allocation.charge.amount,
        }
        for allocation in allocations
    ]


def build_snapshot(payment, allocations, surplus, number, issued_at):
    """Todo lo que imprime el recibo. No guarda el número de operación completo."""
    unit = Unit.objects.select_related("tower__condominium").get(pk=payment.unit_id)
    return {
        "version": SNAPSHOT_VERSION,
        "number": number,
        "issued_at": issued_at.isoformat(),
        "condominium": unit.tower.condominium.name,
        "tower": unit.tower.name,
        "unit": unit.code,
        "holder": _primary_holder(unit.pk),
        "amount": _money(payment.amount),
        "method": payment.method,
        "method_label": Payment.Method(payment.method).label,
        "operation": mask_operation(payment.operation_number),
        "paid_at": payment.paid_at.isoformat(),
        "periods": _periods(allocations),
        "credit_remainder": _money(surplus),
    }


def issue_receipt(payment, allocations, surplus, *, actor=None, ip=None):
    """Emite el recibo de un pago recién aprobado. Los pagos LEGACY no emiten."""
    if payment.method == Payment.Method.LEGACY:
        return None
    issued_at = _ahora()
    number = next_number(timezone.localtime(issued_at).year)
    snapshot = build_snapshot(payment, allocations, surplus, number, issued_at)
    receipt = Receipt.objects.create(
        payment=payment, number=number, issued_at=issued_at, snapshot=snapshot
    )
    log_event(
        "RECEIPT_ISSUED",
        "Receipt",
        receipt.pk,
        actor=actor,
        changes={
            "number": number,
            "payment": str(payment.pk),
            "unit": snapshot["unit"],
            "amount": snapshot["amount"],
        },
        ip=ip,
    )
    return receipt
