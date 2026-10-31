"""Reglas de cobranza: tarifas, cargos, pagos con comprobante, saldos y morosidad.

Dependencias: `finances` conoce a `properties`, nunca al revés. El saldo de un cargo
es `monto - asignaciones de pagos aprobados` y el saldo a favor de una unidad es lo
aprobado menos lo asignado: ambos se derivan, nunca se guardan.

Orden de bloqueo (evita interbloqueos): unidad, luego pago, luego cargos. Solo el
registro de pagos bloquea la torre (para detectar duplicados sin carreras).
"""

import calendar
import hashlib
import unicodedata
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.audit import log_event
from apps.properties.models import Tower, Unit

from . import receipts
from .models import (
    MAX_VOUCHER_SIZE,
    Charge,
    Concept,
    FeeRate,
    FinanceSettings,
    Payment,
    PaymentAllocation,
    PaymentVoucher,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
DEFAULT_DELINQUENCY_THRESHOLD = 4
MANUAL_CONCEPTS = frozenset(
    {Concept.ORDINARY_FEE, Concept.EXTRAORDINARY_FEE, Concept.ADJUSTMENT}
)
# Conceptos con tarifa propia: por ahora solo la cuota ordinaria.
FEE_RATE_CONCEPTS = frozenset({Concept.ORDINARY_FEE})
ONE_DAY = timedelta(days=1)
MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)
# Orden de aplicación de pagos y de saldo a favor: el cargo más antiguo primero.
CHARGE_ORDER = ("due_date", "period", "created_at", "id")
PAYMENT_METHODS = frozenset(
    {
        Payment.Method.TRANSFER,
        Payment.Method.DEPOSIT,
        Payment.Method.YAPE,
        Payment.Method.PLIN,
    }
)
MAX_VOUCHERS_PER_PAYMENT = 3
MAX_OPERATION_NUMBER_LENGTH = 50
MAX_REASON_LENGTH = 255
# Tipo real del archivo, por su firma: (tipo MIME, extensión).
VOUCHER_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"%PDF-", "application/pdf", ".pdf"),
)


class FinancesError(Exception):
    """Base de los errores de reglas de finanzas."""


class InvalidPeriodError(FinancesError):
    """El periodo no es una fecha válida o es posterior al mes actual."""


class FeeRateNotFoundError(FinancesError):
    """No hay una tarifa vigente para generar los cargos."""


class InvalidFeeRateError(FinancesError):
    """Los datos de la tarifa no cumplen las reglas."""


class InvalidChargeError(FinancesError):
    """Los datos del cargo no cumplen las reglas."""


class ChargeAlreadyExistsError(FinancesError):
    """Ya existe la cuota ordinaria de esa unidad y ese periodo."""


class ChargeNotOpenError(FinancesError):
    """Solo se pueden anular cargos abiertos."""


class ChargeHasAllocationsError(FinancesError):
    """El cargo ya tiene pagos aplicados y no se puede anular."""


class InvalidPaymentError(FinancesError):
    """Los datos del pago o del motivo no cumplen las reglas."""


class DuplicatePaymentError(FinancesError):
    """Ya hay un pago pendiente o aprobado con el mismo medio, operación y monto."""

    def __init__(self, message, existing_payment_id=None):
        super().__init__(message)
        self.existing_payment_id = existing_payment_id


class PaymentNotPendingError(FinancesError):
    """La acción solo aplica a pagos pendientes."""


class PaymentNotApprovedError(FinancesError):
    """Solo se anulan pagos aprobados (uno pendiente se rechaza)."""


class InvalidVoucherError(FinancesError):
    """El archivo no es un comprobante válido (tipo real, vacío o demasiado grande)."""


class VoucherLimitError(FinancesError):
    """El pago ya tiene el máximo de comprobantes."""


class GenerationResult(NamedTuple):
    period: date
    created: int
    existing: int
    skipped: int


class DelinquentUnit(NamedTuple):
    unit: Unit
    months: int
    overdue_amount: Decimal


class StatementLine(NamedTuple):
    charge: Charge
    allocated: Decimal
    pending: Decimal
    is_overdue: bool


class Statement(NamedTuple):
    unit: Unit
    as_of: date
    lines: list
    pending_balance: Decimal
    overdue_balance: Decimal
    months_of_debt: int
    threshold: int
    is_delinquent: bool
    credit_balance: Decimal


class PaymentApproval(NamedTuple):
    payment: Payment
    allocations: list
    charges_covered: int
    surplus: Decimal


class VoucherAttachment(NamedTuple):
    voucher: PaymentVoucher
    # Otros pagos de la misma torre con el mismo archivo: es un aviso, no un bloqueo.
    duplicate_payment_ids: tuple


# --- Fechas y dinero ---------------------------------------------------------


def first_of_month(value):
    if not isinstance(value, date):
        raise InvalidPeriodError("El periodo debe ser una fecha.")
    return value.replace(day=1)


def last_day_of_month(value):
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def to_money(value, error=InvalidChargeError):
    """Convierte a Decimal positivo de 2 decimales; rechaza float y valores raros."""
    if value is None or isinstance(value, (bool, float)):
        raise error("El monto debe ser un número decimal positivo.")
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        raise error("El monto no es un número válido.") from None
    if not amount.is_finite():
        raise error("El monto no es un número válido.")
    if amount != amount.quantize(CENT):
        raise error("El monto admite como máximo 2 decimales.")
    if amount <= 0:
        raise error("El monto debe ser mayor que cero.")
    return amount.quantize(CENT)


# --- Configuración y tarifas -------------------------------------------------


def get_or_create_settings(tower):
    return FinanceSettings.objects.get_or_create(tower=tower)[0]


def delinquency_threshold(tower):
    """Umbral de morosidad de la torre; si no hay configuración, el valor por defecto."""
    value = (
        FinanceSettings.objects.filter(tower=tower)
        .values_list("delinquency_threshold", flat=True)
        .first()
    )
    return value or DEFAULT_DELINQUENCY_THRESHOLD


def current_fee_rate(tower, concept, on_date):
    """Tarifa vigente en la fecha; si hay varias, la de inicio más reciente."""
    return (
        FeeRate.objects.filter(tower=tower, concept=concept, valid_from__lte=on_date)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=on_date))
        .order_by("-valid_from")
        .first()
    )


def create_fee_rate(
    tower, concept, amount, valid_from, valid_to=None, *, actor=None, ip=None
):
    """Crea una tarifa. Debe empezar después de la última y cierra la abierta anterior.

    No toca cargos ya emitidos: solo rige para los que se generen desde `valid_from`.
    """
    if concept not in FEE_RATE_CONCEPTS:
        raise InvalidFeeRateError("Ese concepto no tiene tarifa.")
    try:
        amount = to_money(amount)
    except InvalidChargeError as error:
        raise InvalidFeeRateError(str(error)) from None
    if valid_to is not None and valid_to < valid_from:
        raise InvalidFeeRateError(
            "El fin de la tarifa no puede ser anterior al inicio."
        )

    with transaction.atomic():
        rates = list(
            FeeRate.objects.select_for_update().filter(tower=tower, concept=concept)
        )
        if any(rate.valid_from >= valid_from for rate in rates):
            raise InvalidFeeRateError(
                "La nueva tarifa debe empezar después de la última registrada."
            )
        if any(
            rate.valid_to is not None and rate.valid_to >= valid_from for rate in rates
        ):
            raise InvalidFeeRateError("La tarifa se superpone con una ya registrada.")

        previous = next((rate for rate in rates if rate.valid_to is None), None)
        if previous is not None:
            previous.valid_to = valid_from - ONE_DAY
            previous.save(update_fields=["valid_to", "updated_at"])

        rate = FeeRate.objects.create(
            tower=tower,
            concept=concept,
            amount=amount,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        log_event(
            "FEE_RATE_CREATED",
            "FeeRate",
            rate.pk,
            actor=actor,
            changes={
                "tower": tower.name,
                "concept": concept,
                "amount": str(amount),
                "valid_from": valid_from.isoformat(),
                "valid_to": valid_to.isoformat() if valid_to else None,
                "closed_previous": str(previous.pk) if previous else None,
            },
            ip=ip,
        )
    return rate


# --- Generación mensual ------------------------------------------------------


def generate_monthly_charges(
    period, tower=None, *, actor=None, actor_label="", ip=None
):
    """Crea la cuota ordinaria de cada unidad cobrable. Repetirlo no duplica.

    Es todo o nada: si alguna torre no tiene tarifa vigente no se crea ningún cargo.
    """
    period = first_of_month(period)
    if period > first_of_month(timezone.localdate()):
        raise InvalidPeriodError("No se pueden generar cargos de un mes futuro.")

    if tower is not None:
        towers = [tower]
    else:
        towers = list(Tower.objects.filter(units__is_active=True).distinct())

    totals = [0, 0, 0]
    with transaction.atomic():
        for current in towers:
            created, existing, skipped = _generate_for_tower(
                period, current, actor, actor_label, ip
            )
            totals[0] += created
            totals[1] += existing
            totals[2] += skipped
    return GenerationResult(period, *totals)


def _generate_for_tower(period, tower, actor, actor_label, ip):
    rate = current_fee_rate(tower, Concept.ORDINARY_FEE, period)
    if rate is None:
        raise FeeRateNotFoundError(
            f"No hay una tarifa vigente de cuota ordinaria para {tower.name} "
            f"en {period:%Y-%m}."
        )

    active = Unit.objects.filter(tower=tower, is_active=True)
    billable = list(
        active.filter(
            billing_start__isnull=False, billing_start__lte=last_day_of_month(period)
        )
    )
    skipped = active.count() - len(billable)

    existing_ids = set(
        Charge.objects.filter(
            unit__in=billable, concept=Concept.ORDINARY_FEE, period=period
        ).values_list("unit_id", flat=True)
    )
    due_date = last_day_of_month(period)
    description = f"Cuota ordinaria {period:%m/%Y}"
    new_charges = [
        Charge(
            unit=unit,
            concept=Concept.ORDINARY_FEE,
            period=period,
            description=description,
            amount=rate.amount,
            due_date=due_date,
            status=Charge.Status.OPEN,
            source=Charge.Source.SYSTEM,
        )
        for unit in billable
        if unit.pk not in existing_ids
    ]

    scope = Charge.objects.filter(
        unit__tower=tower, concept=Concept.ORDINARY_FEE, period=period
    )
    before = scope.count()
    Charge.objects.bulk_create(new_charges, ignore_conflicts=True)
    created = scope.count() - before
    _apply_credit_to_new_charges(new_charges, actor, actor_label, ip)

    log_event(
        "CHARGES_GENERATED",
        "Tower",
        tower.pk,
        actor=actor,
        actor_label=actor_label,
        changes={
            "period": period.isoformat(),
            "created": created,
            "existing": len(existing_ids),
            "skipped": skipped,
            "amount": str(rate.amount),
        },
        ip=ip,
    )
    return created, len(existing_ids), skipped


def _apply_credit_to_new_charges(new_charges, actor, actor_label, ip):
    """Aplica el saldo a favor solo a las cuotas recién creadas.

    Una sola consulta dice qué unidades tienen saldo; las demás no cuestan nada más.
    """
    if not new_charges:
        return
    with_credit = units_with_credit([charge.unit_id for charge in new_charges])
    if not with_credit:
        return
    for charge in sorted(new_charges, key=lambda item: item.unit.code):
        if charge.unit_id in with_credit:
            apply_credit(
                charge.unit,
                [charge.pk],
                actor=actor,
                actor_label=actor_label,
                ip=ip,
            )


# --- Cargos manuales y anulación --------------------------------------------


def create_manual_charge(
    unit,
    concept,
    period,
    amount,
    *,
    description="",
    due_date=None,
    actor=None,
    ip=None,
):
    """Crea un cargo a mano (extraordinario, ajuste o una cuota que faltó)."""
    if concept not in MANUAL_CONCEPTS:
        raise InvalidChargeError("Ese concepto no se puede crear manualmente.")
    if not unit.is_active:
        raise InvalidChargeError("No se puede cobrar a una unidad inactiva.")
    amount = to_money(amount)
    period = first_of_month(period)
    due_date = due_date or last_day_of_month(period)
    if due_date < period:
        raise InvalidChargeError("El vencimiento no puede ser anterior al periodo.")

    if (
        concept == Concept.ORDINARY_FEE
        and Charge.objects.filter(unit=unit, concept=concept, period=period).exists()
    ):
        raise ChargeAlreadyExistsError("Esa unidad ya tiene la cuota de ese periodo.")

    try:
        with transaction.atomic():
            charge = Charge.objects.create(
                unit=unit,
                concept=concept,
                period=period,
                description=(description or "").strip()[:255],
                amount=amount,
                due_date=due_date,
                status=Charge.Status.OPEN,
                source=Charge.Source.MANUAL,
            )
            log_event(
                "CHARGE_CREATED",
                "Charge",
                charge.pk,
                actor=actor,
                changes={
                    "unit": unit.code,
                    "concept": concept,
                    "period": period.isoformat(),
                    "amount": str(amount),
                    "source": Charge.Source.MANUAL,
                },
                ip=ip,
            )
            if units_with_credit([unit.pk]):
                apply_credit(unit, [charge.pk], actor=actor, ip=ip)
    except IntegrityError:
        raise ChargeAlreadyExistsError(
            "Esa unidad ya tiene la cuota de ese periodo."
        ) from None
    return charge


def void_charge(charge, reason, *, actor=None, ip=None):
    """Anula un cargo abierto sin pagos aplicados. Nunca se borra."""
    reason = (reason or "").strip()
    if not reason:
        raise InvalidChargeError("Indica el motivo de la anulación.")

    with transaction.atomic():
        locked = (
            Charge.objects.select_for_update().select_related("unit").get(pk=charge.pk)
        )
        if locked.status != Charge.Status.OPEN:
            raise ChargeNotOpenError("Solo se pueden anular cargos abiertos.")
        if allocated_totals([locked.pk]).get(locked.pk, ZERO) > 0:
            raise ChargeHasAllocationsError(
                "El cargo tiene pagos aplicados; anula primero el pago."
            )
        locked.status = Charge.Status.VOID
        locked.save(update_fields=["status", "updated_at"])
        log_event(
            "CHARGE_VOIDED",
            "Charge",
            locked.pk,
            actor=actor,
            changes={
                "unit": locked.unit.code,
                "concept": locked.concept,
                "period": locked.period.isoformat(),
                "amount": str(locked.amount),
                "reason": reason[:255],
            },
            ip=ip,
        )
    charge.status = Charge.Status.VOID
    return locked


# --- Saldos y morosidad ------------------------------------------------------


def allocated_totals(charge_ids):
    """Monto aplicado por cargo: `{charge_id: Decimal}`.

    Suma solo las asignaciones de pagos aprobados: al anular un pago dejan de contar
    (sin borrarse) y el cargo vuelve a quedar pendiente.
    """
    charge_ids = list(charge_ids)
    if not charge_ids:
        return {}
    rows = (
        PaymentAllocation.objects.filter(
            charge_id__in=charge_ids, payment__status=Payment.Status.APPROVED
        )
        .order_by()
        .values("charge_id")
        .annotate(total=Sum("amount"))
    )
    return {row["charge_id"]: row["total"] for row in rows}


def _pending_by_charge(charges):
    allocated = allocated_totals([charge.pk for charge in charges])
    return {
        charge.pk: max(charge.amount - allocated.get(charge.pk, ZERO), ZERO)
        for charge in charges
    }


def pending_amounts(charges):
    """Pendiente de cada cargo: `{charge_id: Decimal}`. Solo los abiertos deben algo."""
    charges = list(charges)
    pending = _pending_by_charge(charges)
    return {
        charge.pk: pending[charge.pk] if charge.status == Charge.Status.OPEN else ZERO
        for charge in charges
    }


def pending_amount(charge):
    return _pending_by_charge([charge])[charge.pk]


def _open_charges(unit):
    return list(Charge.objects.filter(unit=unit, status=Charge.Status.OPEN))


def pending_balance(unit):
    """Lo que la unidad debe en total (cargos abiertos menos lo aplicado)."""
    charges = _open_charges(unit)
    pending = _pending_by_charge(charges)
    return sum(pending.values(), ZERO)


def _overdue(charges, as_of):
    pending = _pending_by_charge(charges)
    return [c for c in charges if c.due_date < as_of and pending[c.pk] > 0], pending


def overdue_balance(unit, as_of=None):
    as_of = as_of or timezone.localdate()
    overdue, pending = _overdue(_open_charges(unit), as_of)
    return sum((pending[c.pk] for c in overdue), ZERO)


def months_of_debt(unit, as_of=None):
    """Cantidad de cargos vencidos con saldo pendiente (vencen tras su fecha límite)."""
    as_of = as_of or timezone.localdate()
    return len(_overdue(_open_charges(unit), as_of)[0])


def is_delinquent(unit, as_of=None):
    return months_of_debt(unit, as_of) >= delinquency_threshold(unit.tower)


def delinquent_units(tower, as_of=None):
    """Unidades de la torre con meses de deuda >= umbral, de mayor a menor deuda."""
    as_of = as_of or timezone.localdate()
    charges = list(
        Charge.objects.filter(
            unit__tower=tower, status=Charge.Status.OPEN, due_date__lt=as_of
        ).select_related("unit")
    )
    pending = _pending_by_charge(charges)
    months = defaultdict(int)
    amounts = defaultdict(lambda: ZERO)
    units = {}
    for charge in charges:
        if pending[charge.pk] > 0:
            months[charge.unit_id] += 1
            amounts[charge.unit_id] += pending[charge.pk]
            units[charge.unit_id] = charge.unit
    threshold = delinquency_threshold(tower)
    result = [
        DelinquentUnit(units[unit_id], count, amounts[unit_id])
        for unit_id, count in months.items()
        if count >= threshold
    ]
    result.sort(key=lambda item: (-item.months, item.unit.floor, item.unit.number))
    return result


def unit_statement(unit, as_of=None):
    """Estado de cuenta de la unidad: cargos no anulados, saldos y morosidad."""
    as_of = as_of or timezone.localdate()
    charges = list(Charge.objects.filter(unit=unit).exclude(status=Charge.Status.VOID))
    allocated = allocated_totals([charge.pk for charge in charges])
    pending = pending_amounts(charges)

    lines = [
        StatementLine(
            charge=charge,
            allocated=allocated.get(charge.pk, ZERO),
            pending=pending[charge.pk],
            is_overdue=pending[charge.pk] > 0 and charge.due_date < as_of,
        )
        for charge in charges
    ]
    overdue = [line for line in lines if line.is_overdue]
    threshold = delinquency_threshold(unit.tower)
    return Statement(
        unit=unit,
        as_of=as_of,
        lines=lines,
        pending_balance=sum((line.pending for line in lines), ZERO),
        overdue_balance=sum((line.pending for line in overdue), ZERO),
        months_of_debt=len(overdue),
        threshold=threshold,
        is_delinquent=len(overdue) >= threshold,
        credit_balance=credit_balance(unit),
    )


# --- Pagos -------------------------------------------------------------------


def normalize_operation_number(value):
    """Solo letras y dígitos, en mayúsculas: ` op-00 12 ` y `OP0012` son lo mismo."""
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return "".join(char for char in text if char.isalnum()).upper()


def _clean_reason(reason, message):
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise InvalidPaymentError(message)
    return reason[:MAX_REASON_LENGTH]


def _lock_unit(unit_id):
    """Primer bloqueo de toda operación que mueve saldos de la unidad."""
    return Unit.objects.select_for_update().get(pk=unit_id)


def _unit_code(unit_id):
    return Unit.objects.filter(pk=unit_id).values_list("code", flat=True).get()


def _sync(payment, locked):
    """Deja el objeto que recibió el servicio con el estado ya guardado."""
    for name in (
        "status",
        "reviewed_by",
        "reviewed_at",
        "rejection_reason",
        "updated_at",
    ):
        setattr(payment, name, getattr(locked, name))


def submit_payment(
    unit,
    amount,
    method,
    operation_number,
    paid_at,
    *,
    source=Payment.Source.RESIDENT,
    submitted_by=None,
    actor=None,
    ip=None,
):
    """Registra un pago PENDIENTE. Un duplicado pendiente o aprobado se rechaza.

    El duplicado se busca por medio + número de operación normalizado + monto en toda
    la torre; se bloquea la fila de la torre para que dos registros simultáneos no
    se crucen. Quién puede registrar para cada unidad lo decide la capa de API.
    """
    amount = to_money(amount, InvalidPaymentError)
    if source not in Payment.Source.values:
        raise InvalidPaymentError("El origen del pago no es válido.")
    if method == Payment.Method.LEGACY:
        if source != Payment.Source.IMPORT:
            raise InvalidPaymentError("El medio LEGACY es solo para la importación.")
    elif method not in PAYMENT_METHODS:
        raise InvalidPaymentError("El medio de pago no es válido.")
    if not isinstance(paid_at, date) or isinstance(paid_at, datetime):
        raise InvalidPaymentError("La fecha del pago debe ser una fecha.")
    if paid_at > timezone.localdate():
        raise InvalidPaymentError("La fecha del pago no puede ser futura.")
    operation = normalize_operation_number(operation_number)
    if len(operation) > MAX_OPERATION_NUMBER_LENGTH:
        raise InvalidPaymentError("El número de operación es demasiado largo.")
    if not operation and method != Payment.Method.LEGACY:
        raise InvalidPaymentError("Indica el número de operación.")
    if not unit.is_active:
        raise InvalidPaymentError(
            "No se puede registrar un pago de una unidad inactiva."
        )

    with transaction.atomic():
        Tower.objects.select_for_update().get(pk=unit.tower_id)
        if operation:
            existing = (
                Payment.objects.filter(
                    unit__tower_id=unit.tower_id,
                    method=method,
                    operation_number=operation,
                    amount=amount,
                    status__in=(Payment.Status.PENDING, Payment.Status.APPROVED),
                )
                .values_list("pk", flat=True)
                .first()
            )
            if existing is not None:
                raise DuplicatePaymentError(
                    "Ya existe un pago pendiente o aprobado con ese medio, "
                    "número de operación y monto.",
                    existing_payment_id=existing,
                )
        payment = Payment.objects.create(
            unit=unit,
            amount=amount,
            method=method,
            operation_number=operation,
            paid_at=paid_at,
            status=Payment.Status.PENDING,
            submitted_by=submitted_by or actor,
            source=source,
        )
        log_event(
            "PAYMENT_SUBMITTED",
            "Payment",
            payment.pk,
            actor=actor,
            changes={
                "unit": unit.code,
                "amount": str(amount),
                "method": method,
                "operation_last4": operation[-4:],
                "paid_at": paid_at.isoformat(),
                "source": source,
            },
            ip=ip,
        )
    return payment


def payments_with_same_operation(payment):
    """Ids de otros pagos vigentes de la torre con el mismo medio y operación.

    Son pagos pendientes o aprobados con otro monto (con el mismo monto habría sido
    un duplicado y se habría rechazado al registrar).
    """
    if not payment.operation_number:
        return []
    return list(
        Payment.objects.filter(
            unit__tower_id=payment.unit.tower_id,
            method=payment.method,
            operation_number=payment.operation_number,
            status__in=(Payment.Status.PENDING, Payment.Status.APPROVED),
        )
        .exclude(pk=payment.pk)
        .order_by("created_at", "id")
        .values_list("pk", flat=True)
    )


# --- Comprobantes ------------------------------------------------------------


def _read_voucher(content):
    """Lee como máximo 5 MB + 1 byte; así un archivo enorme no llena la memoria."""
    if isinstance(content, (bytes, bytearray)):
        data = bytes(content)
    elif hasattr(content, "read"):
        if hasattr(content, "seek"):
            content.seek(0)
        data = content.read(MAX_VOUCHER_SIZE + 1)
        if not isinstance(data, (bytes, bytearray)):
            raise InvalidVoucherError("El archivo no se pudo leer.")
        data = bytes(data)
    else:
        raise InvalidVoucherError("El comprobante debe ser un archivo.")
    if not data:
        raise InvalidVoucherError("El archivo está vacío.")
    if len(data) > MAX_VOUCHER_SIZE:
        raise InvalidVoucherError("El archivo supera el máximo de 5 MB.")
    return data


def _voucher_type(data):
    """Tipo REAL por la firma del contenido; el nombre y el tipo del cliente no cuentan."""
    for signature, mime, extension in VOUCHER_SIGNATURES:
        if data.startswith(signature):
            return mime, extension
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise InvalidVoucherError("El archivo debe ser una imagen JPG, PNG, WebP o un PDF.")


def attach_voucher(payment, content, *, actor=None, ip=None):
    """Guarda un comprobante de un pago PENDIENTE en el directorio privado.

    `content` son bytes o un archivo (p. ej. `UploadedFile`). Un `sha256` repetido en
    otro pago de la torre no bloquea: se informa en `duplicate_payment_ids`.
    """
    data = _read_voucher(content)
    content_type, extension = _voucher_type(data)
    digest = hashlib.sha256(data).hexdigest()
    saved_name = None
    storage = PaymentVoucher._meta.get_field("file").storage
    try:
        with transaction.atomic():
            locked = Payment.objects.select_for_update().get(pk=payment.pk)
            if locked.status != Payment.Status.PENDING:
                raise PaymentNotPendingError(
                    "Solo se adjunta un comprobante a un pago pendiente."
                )
            if locked.vouchers.count() >= MAX_VOUCHERS_PER_PAYMENT:
                raise VoucherLimitError(
                    f"Un pago admite como máximo {MAX_VOUCHERS_PER_PAYMENT} comprobantes."
                )
            if locked.vouchers.filter(sha256=digest).exists():
                raise InvalidVoucherError("Ese archivo ya está adjunto a este pago.")
            tower_id = (
                Unit.objects.filter(pk=locked.unit_id)
                .values_list("tower_id", flat=True)
                .get()
            )
            others = (
                PaymentVoucher.objects.filter(
                    sha256=digest, payment__unit__tower_id=tower_id
                )
                .exclude(payment=locked)
                .order_by("created_at")
                .values_list("payment_id", flat=True)
            )
            duplicates = tuple(dict.fromkeys(others))

            voucher = PaymentVoucher(
                payment=locked,
                sha256=digest,
                content_type=content_type,
                size=len(data),
            )
            voucher.file.save(
                f"{uuid.uuid4().hex}{extension}", ContentFile(data), save=False
            )
            saved_name = voucher.file.name
            voucher.save()
            log_event(
                "VOUCHER_ATTACHED",
                "PaymentVoucher",
                voucher.pk,
                actor=actor,
                changes={
                    "payment": str(locked.pk),
                    "content_type": content_type,
                    "size": len(data),
                    "sha256": digest,
                    "vouchers": locked.vouchers.count(),
                    "duplicates": len(duplicates),
                },
                ip=ip,
            )
    except Exception:
        if saved_name:
            storage.delete(saved_name)
        raise
    return VoucherAttachment(voucher, duplicates)


# --- Saldo a favor -----------------------------------------------------------


def _credit_payments(unit_ids):
    """Pagos aprobados con resto sin aplicar (`remaining`), en una sola consulta.

    El saldo a favor de una unidad es la suma de esos restos. `remaining >= 0,01`
    evita falsos positivos por redondeo en SQLite.
    """
    allocated = (
        PaymentAllocation.objects.filter(payment=OuterRef("pk"))
        .order_by()
        .values("payment")
        .annotate(total=Sum("amount"))
        .values("total")
    )
    return (
        Payment.objects.filter(
            unit_id__in=list(unit_ids), status=Payment.Status.APPROVED
        )
        .annotate(
            allocated_total=Coalesce(
                Subquery(allocated, output_field=MONEY_FIELD),
                Value(ZERO, output_field=MONEY_FIELD),
            )
        )
        .annotate(
            remaining=ExpressionWrapper(
                F("amount") - F("allocated_total"), output_field=MONEY_FIELD
            )
        )
        .filter(remaining__gte=CENT)
    )


def credit_balance(unit):
    """Saldo a favor: pagos aprobados menos sus asignaciones. Nunca negativo."""
    return sum((p.remaining for p in _credit_payments([unit.pk])), ZERO)


def units_with_credit(unit_ids):
    """Ids de las unidades (entre `unit_ids`) con saldo a favor; una sola consulta."""
    return set(_credit_payments(unit_ids).order_by().values_list("unit_id", flat=True))


def apply_credit(unit, charge_ids=None, *, actor=None, actor_label="", ip=None):
    """Aplica el saldo a favor a cargos abiertos con pendiente; devuelve lo aplicado.

    Con `charge_ids` solo toca esos cargos (así se aplica a los nuevos); sin ellos,
    a todos los pendientes de la unidad. Va del cargo más antiguo al más nuevo y
    consume primero el pago más antiguo con saldo.
    """
    with transaction.atomic():
        locked_unit = _lock_unit(unit.pk)
        charges = Charge.objects.select_for_update().filter(
            unit_id=locked_unit.pk, status=Charge.Status.OPEN
        )
        if charge_ids is not None:
            charges = charges.filter(pk__in=list(charge_ids))
        charges = list(charges.order_by(*CHARGE_ORDER))
        if not charges:
            return ZERO
        sources = [
            [payment, payment.remaining]
            for payment in _credit_payments([locked_unit.pk]).order_by(
                "paid_at", "created_at", "id"
            )
        ]
        if not sources:
            return ZERO

        pending = _pending_by_charge(charges)
        existing = {
            (row.payment_id, row.charge_id): row
            for row in PaymentAllocation.objects.filter(
                payment__in=[item[0] for item in sources], charge__in=charges
            )
        }
        created = []
        applied = ZERO
        charges_touched = set()
        payments_used = set()
        for charge in charges:
            need = pending[charge.pk]
            for item in sources:
                if need <= 0:
                    break
                payment, left = item
                if left <= 0:
                    continue
                take = min(need, left)
                row = existing.get((payment.pk, charge.pk))
                if row is not None:
                    row.amount += take
                    row.save(update_fields=["amount", "updated_at"])
                else:
                    created.append(
                        PaymentAllocation(payment=payment, charge=charge, amount=take)
                    )
                item[1] = left - take
                need -= take
                applied += take
                charges_touched.add(charge.pk)
                payments_used.add(payment.pk)
        PaymentAllocation.objects.bulk_create(created)
        if applied > 0:
            log_event(
                "CREDIT_APPLIED",
                "Unit",
                locked_unit.pk,
                actor=actor,
                actor_label=actor_label,
                changes={
                    "unit": locked_unit.code,
                    "applied": str(applied),
                    "charges": len(charges_touched),
                    "payments": len(payments_used),
                },
                ip=ip,
            )
    return applied


# --- Aprobación, rechazo y anulación ----------------------------------------


def approve_payment(payment, *, actor=None, ip=None):
    """Aprueba un pago pendiente y lo aplica al cargo más antiguo primero.

    Bloquea la unidad, el pago y los cargos abiertos. Soporta pagos parciales; lo que
    sobra queda como saldo a favor (derivado, no guardado).
    """
    with transaction.atomic():
        unit = _lock_unit(payment.unit_id)
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.Status.PENDING:
            raise PaymentNotPendingError("Solo se aprueban pagos pendientes.")
        charges = list(
            Charge.objects.select_for_update()
            .filter(unit_id=unit.pk, status=Charge.Status.OPEN)
            .order_by(*CHARGE_ORDER)
        )
        pending = _pending_by_charge(charges)

        remaining = locked.amount
        allocations = []
        covered = 0
        for charge in charges:
            if remaining <= 0:
                break
            need = pending[charge.pk]
            if need <= 0:
                continue
            take = min(need, remaining)
            allocations.append(
                PaymentAllocation(payment=locked, charge=charge, amount=take)
            )
            remaining -= take
            if take == need:
                covered += 1
        PaymentAllocation.objects.bulk_create(allocations)

        locked.status = Payment.Status.APPROVED
        locked.reviewed_by = actor
        locked.reviewed_at = timezone.now()
        locked.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
        )
        log_event(
            "PAYMENT_APPROVED",
            "Payment",
            locked.pk,
            actor=actor,
            changes={
                "unit": unit.code,
                "amount": str(locked.amount),
                "allocations": len(allocations),
                "charges_covered": covered,
                "surplus": str(remaining),
            },
            ip=ip,
        )
        # El recibo nace en la misma transacción que la aplicación a cargos.
        receipts.issue_receipt(locked, allocations, remaining, actor=actor, ip=ip)
    _sync(payment, locked)
    return PaymentApproval(payment, allocations, covered, remaining)


def reject_payment(payment, reason, *, actor=None, ip=None):
    """Rechaza un pago pendiente con motivo. No aplica nada."""
    reason = _clean_reason(reason, "Indica el motivo del rechazo.")
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.Status.PENDING:
            raise PaymentNotPendingError("Solo se rechazan pagos pendientes.")
        locked.status = Payment.Status.REJECTED
        locked.rejection_reason = reason
        locked.reviewed_by = actor
        locked.reviewed_at = timezone.now()
        locked.save(
            update_fields=[
                "status",
                "rejection_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )
        log_event(
            "PAYMENT_REJECTED",
            "Payment",
            locked.pk,
            actor=actor,
            changes={
                "unit": _unit_code(locked.unit_id),
                "amount": str(locked.amount),
                "reason": reason,
            },
            ip=ip,
        )
    _sync(payment, locked)
    return payment


def void_payment(payment, reason, *, actor=None, ip=None):
    """Anula un pago aprobado. Sus asignaciones se conservan pero dejan de contar.

    Los cargos que cubría vuelven a quedar pendientes y el saldo a favor de la unidad
    se recalcula solo (todo se deriva de pagos aprobados).
    """
    reason = _clean_reason(reason, "Indica el motivo de la anulación.")
    with transaction.atomic():
        unit = _lock_unit(payment.unit_id)
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.Status.APPROVED:
            raise PaymentNotApprovedError(
                "Solo se anulan pagos aprobados; un pago pendiente se rechaza."
            )
        released = PaymentAllocation.objects.filter(payment=locked).aggregate(
            total=Sum("amount")
        )["total"]
        locked.status = Payment.Status.VOID
        locked.save(update_fields=["status", "updated_at"])
        log_event(
            "PAYMENT_VOIDED",
            "Payment",
            locked.pk,
            actor=actor,
            changes={
                "unit": unit.code,
                "amount": str(locked.amount),
                "released": str((released or ZERO).quantize(CENT)),
                "reason": reason,
            },
            ip=ip,
        )
    _sync(payment, locked)
    return payment
