"""API de finanzas. Las vistas validan, autorizan y delegan en `services`."""

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, Throttled
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdmin
from apps.accounts.security import client_ip
from apps.accounts.views import api_error
from apps.core.pagination import StandardPagination
from apps.core.profile import get_roles
from apps.properties.models import Tower, Unit
from apps.properties.permissions import STAFF_ROLES
from apps.properties.views import paginated, validation_error, visible_units

from . import services
from .models import Charge, FeeRate, Payment
from .permissions import REVIEW_ROLES, IsStaff, IsTreasurerOrAdmin
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
    PaymentCreateSerializer,
    PaymentFilterSerializer,
    PaymentSerializer,
    PaymentVoucherSerializer,
    StatementSerializer,
    VoidSerializer,
    VoucherQuerySerializer,
)
from .throttles import VoucherUploadThrottle


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


# --- Pagos -------------------------------------------------------------------

# Tipos de comprobante que se sirven tal cual; cualquier otro baja como binario.
VOUCHER_DOWNLOAD_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


def payments_queryset():
    return Payment.objects.select_related("unit").prefetch_related(
        "vouchers", "allocations"
    )


def resident_units(user):
    """Unidades donde el usuario tiene una ocupación vigente (sea o no de gestión)."""
    return (
        Unit.objects.select_related("tower")
        .filter(occupancies__person__user=user, occupancies__end_date__isnull=True)
        .distinct()
    )


def readable_units(user, roles):
    return (
        Unit.objects.select_related("tower")
        if roles & STAFF_ROLES
        else resident_units(user)
    )


def visible_payments(user, roles):
    """La gestión ve todos los pagos; un residente, solo los de sus unidades."""
    queryset = payments_queryset()
    if roles & STAFF_ROLES:
        return queryset
    return queryset.filter(unit_id__in=resident_units(user).values("pk"))


def payment_context(request, roles):
    return {"request": request, "roles": roles}


def payment_response(
    request, roles, payment_id, http_status=status.HTTP_200_OK, extra=None
):
    """Serializa el pago recién modificado, con sus relaciones al día."""
    payment = payments_queryset().get(pk=payment_id)
    data = PaymentSerializer(payment, context=payment_context(request, roles)).data
    return Response({**data, **(extra or {})}, status=http_status)


def warning(code, detail, ids, roles):
    """Advertencia; solo la gestión ve los ids de los otros pagos."""
    item = {"code": code, "detail": detail}
    if roles & STAFF_ROLES:
        item["payment_ids"] = [str(pk) for pk in ids]
    return item


class PaymentListCreateView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        filters, error = validated_query(PaymentFilterSerializer, request)
        if error:
            return error
        roles = get_roles(request.user)
        queryset = visible_payments(request.user, roles)
        if "unit" in filters:
            unit = get_object_or_404(
                readable_units(request.user, roles), pk=filters["unit"]
            )  # ajena o inexistente -> 404
            queryset = queryset.filter(unit=unit)
        if "status" in filters:
            queryset = queryset.filter(status=filters["status"])
        if "method" in filters:
            queryset = queryset.filter(method=filters["method"])
        if "paid_from" in filters:
            queryset = queryset.filter(paid_at__gte=filters["paid_from"])
        if "paid_to" in filters:
            queryset = queryset.filter(paid_at__lte=filters["paid_to"])

        paginator = StandardPagination()
        page = paginator.paginate_queryset(
            queryset.order_by("-paid_at", "-created_at", "-pk"), request, view=self
        )
        data = PaymentSerializer(
            page, many=True, context=payment_context(request, roles)
        ).data
        return paginator.get_paginated_response(data)

    def post(self, request):
        serializer = PaymentCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        data = serializer.validated_data
        user = request.user
        roles = get_roles(user)
        if roles & REVIEW_ROLES:
            unit = get_object_or_404(
                Unit.objects.select_related("tower"), pk=data["unit"]
            )
            source = Payment.Source.STAFF
        else:
            unit = get_object_or_404(
                resident_units(user), pk=data["unit"]
            )  # ajena -> 404
            source = Payment.Source.RESIDENT
        try:
            payment = services.submit_payment(
                unit,
                data["amount"],
                data["method"],
                data["operation_number"],
                data["paid_at"],
                source=source,
                submitted_by=user,
                actor=user,
                ip=client_ip(request),
            )
        except services.InvalidPaymentError as error:
            return bad_request("invalid_payment", error)
        except services.DuplicatePaymentError as error:
            # Un residente no debe enterarse de pagos de otras unidades.
            errors = {}
            if roles & STAFF_ROLES and error.existing_payment_id:
                errors = {"existing_payment": str(error.existing_payment_id)}
            return api_error(
                "duplicate_payment", str(error), status.HTTP_409_CONFLICT, errors
            )

        warnings = []
        similar = services.payments_with_same_operation(payment)
        if similar:
            warnings.append(
                warning(
                    "same_operation_number",
                    "Otro pago de la torre usa el mismo medio y número de "
                    "operación con otro monto.",
                    similar,
                    roles,
                )
            )
        return payment_response(
            request,
            roles,
            payment.pk,
            status.HTTP_201_CREATED,
            {"warnings": warnings},
        )


class PaymentDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, pk):
        roles = get_roles(request.user)
        payment = get_object_or_404(visible_payments(request.user, roles), pk=pk)
        return Response(
            PaymentSerializer(payment, context=payment_context(request, roles)).data
        )


class PaymentVoucherView(APIView):
    """Subida (multipart) y descarga de comprobantes; nunca hay URL pública."""

    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser,)

    def get_throttles(self):
        if self.request.method == "POST":
            return [VoucherUploadThrottle()]
        return []

    def handle_exception(self, exc):
        if isinstance(exc, Throttled):
            response = api_error(
                "throttled",
                "Subiste demasiados comprobantes. Intenta de nuevo más tarde.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
            if exc.wait is not None:
                response["Retry-After"] = str(int(exc.wait) + 1)
            return response
        return super().handle_exception(exc)

    def post(self, request, pk):
        user = request.user
        roles = get_roles(user)
        payment = get_object_or_404(visible_payments(user, roles), pk=pk)
        if not (roles & REVIEW_ROLES or payment.submitted_by_id == user.pk):
            raise PermissionDenied()
        archivo = request.FILES.get("file")
        if archivo is None:
            return validation_error({"file": ["Adjunta el archivo del comprobante."]})
        try:
            result = services.attach_voucher(
                payment, archivo, actor=user, ip=client_ip(request)
            )
        except services.PaymentNotPendingError as error:
            return conflict("payment_not_pending", error)
        except services.VoucherLimitError as error:
            return conflict("voucher_limit", error)
        except services.InvalidVoucherError as error:
            return bad_request("invalid_voucher", error)

        warnings = []
        if result.duplicate_payment_ids:
            warnings.append(
                warning(
                    "duplicate_voucher",
                    "Ese archivo ya se usó en otro pago de la torre.",
                    result.duplicate_payment_ids,
                    roles,
                )
            )
        data = PaymentVoucherSerializer(result.voucher).data
        return Response({**data, "warnings": warnings}, status=status.HTTP_201_CREATED)

    def get(self, request, pk):
        user = request.user
        roles = get_roles(user)
        payment = get_object_or_404(visible_payments(user, roles), pk=pk)
        # La junta ve el pago, pero solo la administración, tesorería y los
        # residentes de la unidad bajan el comprobante.
        if not (
            roles & REVIEW_ROLES
            or resident_units(user).filter(pk=payment.unit_id).exists()
        ):
            raise PermissionDenied()
        filters, error = validated_query(VoucherQuerySerializer, request)
        if error:
            return error

        vouchers = list(payment.vouchers.all())
        if "voucher" in filters:
            chosen = next((v for v in vouchers if v.pk == filters["voucher"]), None)
        else:
            chosen = vouchers[-1] if vouchers else None
        if chosen is None:
            return api_error(
                "voucher_not_found",
                "El pago no tiene ese comprobante.",
                status.HTTP_404_NOT_FOUND,
            )
        content_type = chosen.content_type
        extension = VOUCHER_DOWNLOAD_TYPES.get(content_type)
        if extension is None:
            content_type, extension = "application/octet-stream", ".bin"
        try:
            handle = chosen.file.open("rb")
        except OSError:
            return api_error(
                "voucher_file_missing",
                "El archivo del comprobante no está disponible.",
                status.HTTP_404_NOT_FOUND,
            )
        response = FileResponse(
            handle,
            as_attachment=True,
            filename=f"comprobante-{vouchers.index(chosen) + 1}{extension}",
            content_type=content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private"
        return response


class PaymentApproveView(APIView):
    permission_classes = (IsTreasurerOrAdmin,)

    def post(self, request, pk):
        payment = get_object_or_404(payments_queryset(), pk=pk)
        try:
            services.approve_payment(payment, actor=request.user, ip=client_ip(request))
        except services.PaymentNotPendingError as error:
            return conflict("payment_not_pending", error)
        return payment_response(request, get_roles(request.user), payment.pk)


class PaymentRejectView(APIView):
    permission_classes = (IsTreasurerOrAdmin,)

    def post(self, request, pk):
        payment = get_object_or_404(payments_queryset(), pk=pk)
        serializer = VoidSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        try:
            services.reject_payment(
                payment,
                serializer.validated_data["reason"],
                actor=request.user,
                ip=client_ip(request),
            )
        except services.PaymentNotPendingError as error:
            return conflict("payment_not_pending", error)
        return payment_response(request, get_roles(request.user), payment.pk)


class PaymentVoidView(APIView):
    permission_classes = (IsAdmin,)

    def post(self, request, pk):
        payment = get_object_or_404(payments_queryset(), pk=pk)
        serializer = VoidSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        try:
            services.void_payment(
                payment,
                serializer.validated_data["reason"],
                actor=request.user,
                ip=client_ip(request),
            )
        except services.PaymentNotApprovedError as error:
            return conflict("payment_not_approved", error)
        return payment_response(request, get_roles(request.user), payment.pk)
