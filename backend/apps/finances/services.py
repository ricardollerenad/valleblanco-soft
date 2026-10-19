"""Reglas de cobranza: tarifas, generación de cargos, anulación, saldos y morosidad.

Dependencias: `finances` conoce a `properties`, nunca al revés. El saldo de un cargo
es `monto - asignaciones`; hasta que existan los pagos (S4) las asignaciones valen 0
y `allocated_totals` es el único punto que habrá que reemplazar.
"""

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.audit import log_event
from apps.properties.models import Tower, Unit

from .models import Charge, Concept, FeeRate, FinanceSettings

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
DEFAULT_DELINQUENCY_THRESHOLD = 4
MANUAL_CONCEPTS = frozenset(
    {Concept.ORDINARY_FEE, Concept.EXTRAORDINARY_FEE, Concept.ADJUSTMENT}
)
# Conceptos con tarifa propia: por ahora solo la cuota ordinaria.
FEE_RATE_CONCEPTS = frozenset({Concept.ORDINARY_FEE})
ONE_DAY = timedelta(days=1)


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


# --- Fechas y dinero ---------------------------------------------------------


def first_of_month(value):
    if not isinstance(value, date):
        raise InvalidPeriodError("El periodo debe ser una fecha.")
    return value.replace(day=1)


def last_day_of_month(value):
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def to_money(value):
    """Convierte a Decimal positivo de 2 decimales; rechaza float y valores raros."""
    if value is None or isinstance(value, (bool, float)):
        raise InvalidChargeError("El monto debe ser un número decimal positivo.")
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        raise InvalidChargeError("El monto no es un número válido.") from None
    if not amount.is_finite():
        raise InvalidChargeError("El monto no es un número válido.")
    if amount != amount.quantize(CENT):
        raise InvalidChargeError("El monto admite como máximo 2 decimales.")
    if amount <= 0:
        raise InvalidChargeError("El monto debe ser mayor que cero.")
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

    S4 lo reemplaza por la suma de `PaymentAllocation` de pagos aprobados.
    """
    return {}


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
    )
