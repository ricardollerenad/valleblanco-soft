"""API de finanzas. Las vistas validan, autorizan y delegan en `services`."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdmin
from apps.accounts.security import client_ip
from apps.accounts.views import api_error
from apps.core.pagination import StandardPagination
from apps.core.profile import get_roles
from apps.properties.models import Tower
from apps.properties.permissions import STAFF_ROLES
from apps.properties.views import paginated, validation_error, visible_units

from . import services
from .models import Charge, FeeRate
from .permissions import IsStaff
from .serializers import (
    AsOfSerializer,
    ChargeCreateSerializer,
    ChargeFilterSerializer,
    ChargeSerializer,
    DelinquencyFilterSerializer,
    DelinquentUnitSerializer,
    FeeRateCreateSerializer,
    FeeRateFilterSerializer,
    FeeRateSerializer,
    GenerateSerializer,
    StatementSerializer,
    VoidSerializer,
)


def conflict(code, error):
    return api_error(code, str(error), status.HTTP_409_CONFLICT)


def bad_request(code, error):
    return api_error(code, str(error), status.HTTP_400_BAD_REQUEST)


def validated_query(serializer_class, request):
    """Valida los parámetros de la URL. Devuelve `(datos, respuesta_de_error)`."""
    serializer = serializer_class(data=request.query_params)
    if not serializer.is_valid():
        return None, validation_error(serializer.errors)
    return serializer.validated_data, None


def with_pending(charges):
    """Pone en cada cargo el saldo pendiente que el serializer espera."""
    charges = list(charges)
    pending = services.pending_amounts(charges)
    for charge in charges:
        charge.pending = pending[charge.pk]
    return charges


def is_staff(user):
    return bool(get_roles(user) & STAFF_ROLES)


# --- Tarifas -----------------------------------------------------------------


class FeeRateListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAdmin()]
        return [IsAuthenticated()]

    def get(self, request):
        filters, error = validated_query(FeeRateFilterSerializer, request)
        if error:
            return error
        queryset = FeeRate.objects.all()
        if "tower" in filters:
            queryset = queryset.filter(tower_id=filters["tower"])
        if "concept" in filters:
            queryset = queryset.filter(concept=filters["concept"])
        return paginated(request, queryset, FeeRateSerializer, self)

    def post(self, request):
        serializer = FeeRateCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        data = serializer.validated_data
        try:
            rate = services.create_fee_rate(
                data["tower"],
                data["concept"],
                data["amount"],
                data["valid_from"],
                data.get("valid_to"),
                actor=request.user,
                ip=client_ip(request),
            )
        except services.InvalidFeeRateError as error:
            return bad_request("invalid_fee_rate", error)
        return Response(FeeRateSerializer(rate).data, status=status.HTTP_201_CREATED)


# --- Cargos ------------------------------------------------------------------


class ChargeListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAdmin()]
        return [IsAuthenticated()]

    def get(self, request):
        filters, error = validated_query(ChargeFilterSerializer, request)
        if error:
            return error

        user = request.user
        units = visible_units(user)
        queryset = Charge.objects.select_related("unit")
        if not is_staff(user):
            # Un residente ve solo lo de sus unidades y nunca los cargos anulados.
            queryset = queryset.filter(unit_id__in=units.values("pk")).exclude(
                status=Charge.Status.VOID
            )
        if "unit" in filters:
            unit = get_object_or_404(units, pk=filters["unit"])  # ajena -> 404
            queryset = queryset.filter(unit=unit)
        if "period" in filters:
            queryset = queryset.filter(period=filters["period"])
        if "status" in filters:
            queryset = queryset.filter(status=filters["status"])
        if "concept" in filters:
            queryset = queryset.filter(concept=filters["concept"])

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            ChargeSerializer(with_pending(page), many=True).data
        )

    def post(self, request):
        serializer = ChargeCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        data = serializer.validated_data
        try:
            charge = services.create_manual_charge(
                data["unit"],
                data["concept"],
                data["period"],
                data["amount"],
                description=data.get("description", ""),
                due_date=data.get("due_date"),
                actor=request.user,
                ip=client_ip(request),
            )
        except services.ChargeAlreadyExistsError as error:
            return conflict("charge_exists", error)
        except services.InvalidChargeError as error:
            return bad_request("invalid_charge", error)
        (charge,) = with_pending([charge])
        return Response(ChargeSerializer(charge).data, status=status.HTTP_201_CREATED)


class ChargeGenerateView(APIView):
    permission_classes = (IsAdmin,)

    def post(self, request):
        serializer = GenerateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        data = serializer.validated_data
        try:
            result = services.generate_monthly_charges(
                data["period"],
                data.get("tower"),
                actor=request.user,
                ip=client_ip(request),
            )
        except services.InvalidPeriodError as error:
            return bad_request("invalid_period", error)
        except services.FeeRateNotFoundError as error:
            return conflict("fee_rate_not_found", error)
        return Response(
            {
                "period": result.period.isoformat(),
                "created": result.created,
                "existing": result.existing,
                "skipped": result.skipped,
            }
        )


class ChargeVoidView(APIView):
    permission_classes = (IsAdmin,)

    def post(self, request, pk):
        charge = get_object_or_404(Charge.objects.select_related("unit"), pk=pk)
        serializer = VoidSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        try:
            voided = services.void_charge(
                charge,
                serializer.validated_data["reason"],
                actor=request.user,
                ip=client_ip(request),
            )
        except services.ChargeNotOpenError as error:
            return conflict("charge_not_open", error)
        except services.ChargeHasAllocationsError as error:
            return conflict("charge_has_allocations", error)
        (voided,) = with_pending([voided])
        return Response(ChargeSerializer(voided).data)


# --- Estado de cuenta y morosidad -------------------------------------------


class UnitStatementView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, pk):
        unit = get_object_or_404(visible_units(request.user), pk=pk)  # ajena -> 404
        filters, error = validated_query(AsOfSerializer, request)
        if error:
            return error
        statement = services.unit_statement(unit, filters.get("as_of"))
        return Response(StatementSerializer(statement).data)


class DelinquencyReportView(APIView):
    permission_classes = (IsStaff,)

    def get(self, request):
        filters, error = validated_query(DelinquencyFilterSerializer, request)
        if error:
            return error
        if "tower" in filters:
            towers = [get_object_or_404(Tower, pk=filters["tower"])]
        else:
            towers = list(Tower.objects.order_by("name"))
        as_of = filters.get("as_of") or timezone.localdate()

        delinquent = []
        for tower in towers:
            delinquent.extend(services.delinquent_units(tower, as_of))
        total = sum((item.overdue_amount for item in delinquent), services.ZERO)
        return Response(
            {
                "as_of": as_of.isoformat(),
                "towers": [
                    {
                        "id": str(tower.pk),
                        "name": tower.name,
                        "delinquency_threshold": services.delinquency_threshold(tower),
                    }
                    for tower in towers
                ],
                "count": len(delinquent),
                "total_overdue": f"{total:.2f}",
                "results": DelinquentUnitSerializer(delinquent, many=True).data,
            }
        )
