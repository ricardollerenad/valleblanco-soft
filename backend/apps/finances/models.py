"""Configuración financiera, tarifas y cargos. Los cargos se anulan, no se borran."""

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel

MONEY = {"max_digits": 12, "decimal_places": 2}


class Concept(models.TextChoices):
    ORDINARY_FEE = "ORDINARY_FEE", "Cuota ordinaria"
    EXTRAORDINARY_FEE = "EXTRAORDINARY_FEE", "Cuota extraordinaria"
    LATE_FEE = "LATE_FEE", "Mora"
    ADJUSTMENT = "ADJUSTMENT", "Ajuste"
    AGREEMENT_INSTALLMENT = "AGREEMENT_INSTALLMENT", "Cuota de convenio"


class FinanceSettings(BaseModel):
    """Reglas de negocio de una torre. Mora y descuento existen pero están apagados."""

    class DueRule(models.TextChoices):
        END_OF_MONTH = "END_OF_MONTH", "Último día del mes"

    class LateFeeType(models.TextChoices):
        FIXED = "FIXED", "Monto fijo"
        PERCENT = "PERCENT", "Porcentaje"

    tower = models.OneToOneField(
        "properties.Tower", on_delete=models.PROTECT, related_name="finance_settings"
    )
    due_rule = models.CharField(
        "regla de vencimiento",
        max_length=20,
        choices=DueRule.choices,
        default=DueRule.END_OF_MONTH,
    )
    delinquency_threshold = models.PositiveSmallIntegerField(
        "umbral de morosidad (cuotas vencidas)",
        default=4,
        validators=[MinValueValidator(1)],
    )
    late_fee_enabled = models.BooleanField("mora activada", default=False)
    late_fee_type = models.CharField(
        "tipo de mora", max_length=10, choices=LateFeeType.choices, blank=True
    )
    late_fee_value = models.DecimalField("valor de la mora", default=0, **MONEY)
    early_discount_enabled = models.BooleanField(
        "descuento por pronto pago activado", default=False
    )
    opening_balance = models.DecimalField("saldo inicial del fondo", default=0, **MONEY)
    opening_balance_date = models.DateField(
        "fecha del saldo inicial", null=True, blank=True
    )

    class Meta:
        verbose_name = "configuración financiera"
        verbose_name_plural = "configuraciones financieras"
        constraints = (
            models.CheckConstraint(
                condition=Q(delinquency_threshold__gte=1),
                name="settings_threshold_at_least_one",
            ),
            models.CheckConstraint(
                condition=Q(late_fee_value__gte=0),
                name="settings_late_fee_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(late_fee_enabled=False) | ~Q(late_fee_type=""),
                name="settings_late_fee_needs_type",
            ),
        )

    def __str__(self):
        return f"Finanzas de {self.tower}"


class FeeRate(BaseModel):
    """Tarifa vigente por concepto. Cambiarla no altera cargos ya emitidos."""

    tower = models.ForeignKey(
        "properties.Tower", on_delete=models.PROTECT, related_name="fee_rates"
    )
    concept = models.CharField("concepto", max_length=30, choices=Concept.choices)
    amount = models.DecimalField("monto", **MONEY)
    valid_from = models.DateField("vigente desde")
    valid_to = models.DateField("vigente hasta", null=True, blank=True)

    class Meta:
        ordering = ("tower__name", "concept", "-valid_from")
        verbose_name = "tarifa"
        verbose_name_plural = "tarifas"
        constraints = (
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="fee_rate_amount_positive"
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True)
                | Q(valid_to__gte=models.F("valid_from")),
                name="fee_rate_end_not_before_start",
            ),
            models.UniqueConstraint(
                fields=("tower", "concept", "valid_from"),
                name="uniq_fee_rate_start_per_tower_concept",
            ),
        )

    def __str__(self):
        return f"{self.get_concept_display()} {self.amount} desde {self.valid_from}"


class Charge(BaseModel):
    """Lo que una unidad debe. El saldo se deriva, nunca se edita."""

    class Status(models.TextChoices):
        OPEN = "OPEN", "Abierto"
        VOID = "VOID", "Anulado"
        REFINANCED = "REFINANCED", "Refinanciado"

    class Source(models.TextChoices):
        SYSTEM = "SYSTEM", "Sistema"
        MANUAL = "MANUAL", "Manual"
        IMPORT = "IMPORT", "Importación"

    unit = models.ForeignKey(
        "properties.Unit", on_delete=models.PROTECT, related_name="charges"
    )
    concept = models.CharField("concepto", max_length=30, choices=Concept.choices)
    period = models.DateField("periodo (primer día del mes)")
    description = models.CharField("descripción", max_length=255, blank=True)
    amount = models.DecimalField("monto", **MONEY)
    due_date = models.DateField("vencimiento")
    status = models.CharField(
        "estado", max_length=12, choices=Status.choices, default=Status.OPEN
    )
    source = models.CharField(
        "origen", max_length=10, choices=Source.choices, default=Source.MANUAL
    )

    class Meta:
        ordering = ("period", "due_date", "unit__floor", "unit__number")
        verbose_name = "cargo"
        verbose_name_plural = "cargos"
        indexes = (models.Index(fields=("unit", "status", "due_date")),)
        constraints = (
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="charge_amount_positive"
            ),
            models.CheckConstraint(
                condition=Q(period__day=1), name="charge_period_is_first_of_month"
            ),
            models.CheckConstraint(
                condition=Q(due_date__gte=models.F("period")),
                name="charge_due_not_before_period",
            ),
            models.UniqueConstraint(
                fields=("unit", "concept", "period"),
                condition=Q(concept="ORDINARY_FEE"),
                name="uniq_ordinary_fee_per_unit_period",
            ),
        )

    def __str__(self):
        return f"{self.unit} {self.concept} {self.period:%Y-%m} {self.amount}"
