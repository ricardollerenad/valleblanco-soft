"""Serializers de finanzas. El dinero sale siempre como texto con 2 decimales."""

import re
from datetime import date

from rest_framework import serializers

from apps.properties.models import Tower, Unit
from apps.properties.serializers import UnitSerializer

from .models import Charge, Concept, FeeRate
from .services import FEE_RATE_CONCEPTS, MANUAL_CONCEPTS

MONEY = {"max_digits": 12, "decimal_places": 2}
PERIOD_PATTERN = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")


def choices_of(allowed):
    return [choice for choice in Concept.choices if choice[0] in allowed]


class PeriodField(serializers.Field):
    """Periodo mensual: acepta `AAAA-MM` o una fecha `AAAA-MM-DD` y da el día 1."""

    default_error_messages = {"invalid": "Usa el formato AAAA-MM o AAAA-MM-DD."}

    def to_internal_value(self, data):
        match = PERIOD_PATTERN.match(data.strip()) if isinstance(data, str) else None
        if match is None:
            self.fail("invalid")
        year, month, day = match.groups()
        try:
            return date(int(year), int(month), int(day or 1)).replace(day=1)
        except ValueError:
            self.fail("invalid")

    def to_representation(self, value):
        return value.isoformat()


# --- Entrada -----------------------------------------------------------------


class FeeRateCreateSerializer(serializers.Serializer):
    tower = serializers.PrimaryKeyRelatedField(queryset=Tower.objects.all())
    concept = serializers.ChoiceField(choices=choices_of(FEE_RATE_CONCEPTS))
    amount = serializers.CharField()
    valid_from = serializers.DateField()
    valid_to = serializers.DateField(required=False, allow_null=True)


class ChargeCreateSerializer(serializers.Serializer):
    unit = serializers.PrimaryKeyRelatedField(
        queryset=Unit.objects.select_related("tower")
    )
    concept = serializers.ChoiceField(choices=choices_of(MANUAL_CONCEPTS))
    period = PeriodField()
    amount = serializers.CharField()
    description = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )
    due_date = serializers.DateField(required=False)


class GenerateSerializer(serializers.Serializer):
    period = PeriodField()
    tower = serializers.PrimaryKeyRelatedField(
        queryset=Tower.objects.all(), required=False
    )


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


# --- Filtros de las listas ---------------------------------------------------


class FeeRateFilterSerializer(serializers.Serializer):
    tower = serializers.UUIDField(required=False)
    concept = serializers.ChoiceField(choices=Concept.choices, required=False)


class ChargeFilterSerializer(serializers.Serializer):
    unit = serializers.UUIDField(required=False)
    period = PeriodField(required=False)
    status = serializers.ChoiceField(choices=Charge.Status.choices, required=False)
    concept = serializers.ChoiceField(choices=Concept.choices, required=False)


class AsOfSerializer(serializers.Serializer):
    as_of = serializers.DateField(required=False)


class DelinquencyFilterSerializer(AsOfSerializer):
    tower = serializers.UUIDField(required=False)


# --- Salida ------------------------------------------------------------------


class FeeRateSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(**MONEY)

    class Meta:
        model = FeeRate
        fields = ("id", "tower", "concept", "amount", "valid_from", "valid_to")
        read_only_fields = fields


class ChargeSerializer(serializers.ModelSerializer):
    """Espera `pending` (Decimal) puesto en cada cargo por la vista."""

    unit_code = serializers.CharField(source="unit.code", read_only=True)
    amount = serializers.DecimalField(**MONEY)
    pending_amount = serializers.DecimalField(source="pending", read_only=True, **MONEY)

    class Meta:
        model = Charge
        fields = (
            "id",
            "unit",
            "unit_code",
            "concept",
            "period",
            "description",
            "amount",
            "pending_amount",
            "due_date",
            "status",
            "source",
        )
        read_only_fields = fields


class StatementLineSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="charge.id")
    concept = serializers.CharField(source="charge.concept")
    period = serializers.DateField(source="charge.period")
    description = serializers.CharField(source="charge.description")
    amount = serializers.DecimalField(source="charge.amount", **MONEY)
    allocated = serializers.DecimalField(**MONEY)
    pending_amount = serializers.DecimalField(source="pending", **MONEY)
    due_date = serializers.DateField(source="charge.due_date")
    status = serializers.CharField(source="charge.status")
    is_overdue = serializers.BooleanField()


class StatementSummarySerializer(serializers.Serializer):
    pending_balance = serializers.DecimalField(**MONEY)
    overdue_balance = serializers.DecimalField(**MONEY)
    months_of_debt = serializers.IntegerField()
    delinquency_threshold = serializers.IntegerField(source="threshold")
    is_delinquent = serializers.BooleanField()


class StatementSerializer(serializers.Serializer):
    unit = UnitSerializer()
    as_of = serializers.DateField()
    summary = StatementSummarySerializer(source="*")
    charges = StatementLineSerializer(source="lines", many=True)


class DelinquentUnitSerializer(serializers.Serializer):
    unit = serializers.UUIDField(source="unit.id")
    unit_code = serializers.CharField(source="unit.code")
    tower = serializers.UUIDField(source="unit.tower.id")
    tower_name = serializers.CharField(source="unit.tower.name")
    months = serializers.IntegerField()
    overdue_amount = serializers.DecimalField(**MONEY)
