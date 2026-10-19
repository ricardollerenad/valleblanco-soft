"""API de propiedades. Todas las vistas filtran por lo que el usuario puede ver."""

import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdmin
from apps.accounts.security import client_ip
from apps.accounts.services import UserAlreadyActiveError
from apps.accounts.views import api_error, invitation_response
from apps.core.audit import log_event
from apps.core.pagination import StandardPagination
from apps.core.profile import get_roles

from .models import Person, RoleAssignment, Unit, UnitOccupancy
from .permissions import STAFF_ROLES
from .serializers import (
    OccupancyCreateSerializer,
    OccupancySerializer,
    OccupancyUpdateSerializer,
    PersonInviteSerializer,
    PersonSerializer,
    RoleAssignmentSerializer,
    UnitSerializer,
)
from .services import (
    InvalidOccupancyError,
    MissingEmailError,
    OccupancyConflictError,
    PersonAlreadyLinkedError,
    RoleAlreadyAssignedError,
    UserLinkedToOtherPersonError,
    add_occupancy,
    assign_role,
    close_occupancy,
    invite_person,
    make_primary,
    revoke_role,
)


def validation_error(errors, detail="Revisa los datos ingresados."):
    return api_error("validation_error", detail, status.HTTP_400_BAD_REQUEST, errors)


def paginated(request, queryset, serializer_class, view):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(serializer_class(page, many=True).data)


def parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def truthy(value):
    return str(value).lower() in {"1", "true", "yes"}


def visible_units(user):
    """Personal de gestión ve todas; propietarios e inquilinos, solo las suyas."""
    queryset = Unit.objects.select_related("tower")
    if get_roles(user).intersection(STAFF_ROLES):
        return queryset
    return queryset.filter(
        occupancies__person__user=user, occupancies__end_date__isnull=True
    ).distinct()


# --- Unidades ----------------------------------------------------------------


class UnitListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        queryset = visible_units(request.user)
        params = request.query_params
        if "tower" in params:
            tower_id = parse_uuid(params["tower"])
            queryset = (
                queryset.filter(tower_id=tower_id) if tower_id else queryset.none()
            )
        if "floor" in params:
            if not params["floor"].isdigit():
                return validation_error({"floor": "Debe ser un número."})
            queryset = queryset.filter(floor=int(params["floor"]))
        if "is_active" in params:
            queryset = queryset.filter(is_active=truthy(params["is_active"]))
        if params.get("q"):
            queryset = queryset.filter(code__icontains=params["q"].strip())
        return paginated(request, queryset, UnitSerializer, self)


class UnitDetailView(APIView):
    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAdmin()]
        return [IsAuthenticated()]

    def get_unit(self, request, pk):
        return get_object_or_404(visible_units(request.user), pk=pk)

    def get(self, request, pk):
        return Response(UnitSerializer(self.get_unit(request, pk)).data)

    def patch(self, request, pk):
        unit = self.get_unit(request, pk)
        serializer = UnitSerializer(unit, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        serializer.save()
        log_event(
            "UNIT_UPDATED",
            "Unit",
            unit.pk,
            actor=request.user,
            changes={"fields": sorted(serializer.validated_data)},
            ip=client_ip(request),
        )
        return Response(UnitSerializer(unit).data)


# --- Personas ----------------------------------------------------------------


class PersonListCreateView(APIView):
    permission_classes = (IsAdmin,)

    def get(self, request):
        queryset = Person.objects.all()
        params = request.query_params
        if params.get("q"):
            term = params["q"].strip()
            queryset = queryset.filter(
                Q(full_name__icontains=term)
                | Q(doc_number__icontains=term)
                | Q(email__icontains=term)
            )
        if "has_user" in params:
            queryset = queryset.filter(user__isnull=not truthy(params["has_user"]))
        return paginated(request, queryset, PersonSerializer, self)

    def post(self, request):
        serializer = PersonSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        person = serializer.save()
        log_event(
            "PERSON_CREATED",
            "Person",
            person.pk,
            actor=request.user,
            ip=client_ip(request),
        )
        return Response(PersonSerializer(person).data, status=status.HTTP_201_CREATED)


class PersonDetailView(APIView):
    permission_classes = (IsAdmin,)

    def get(self, request, pk):
        return Response(PersonSerializer(get_object_or_404(Person, pk=pk)).data)

    def patch(self, request, pk):
        person = get_object_or_404(Person, pk=pk)
        serializer = PersonSerializer(person, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        serializer.save()
        log_event(
            "PERSON_UPDATED",
            "Person",
            person.pk,
            actor=request.user,
            changes={
                "fields": sorted(serializer.validated_data)
            },  # solo nombres, no valores
            ip=client_ip(request),
        )
        return Response(PersonSerializer(person).data)


class PersonInviteView(APIView):
    """Invita a la persona por correo y enlaza su cuenta. El enlace lo ve solo la administración."""

    permission_classes = (IsAdmin,)

    def post(self, request, pk):
        person = get_object_or_404(Person, pk=pk)
        serializer = PersonInviteSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        try:
            result = invite_person(
                person,
                email=serializer.validated_data.get("email"),
                invited_by=request.user,
                ip=client_ip(request),
            )
        except PersonAlreadyLinkedError:
            return api_error(
                "person_already_linked",
                "Esta persona ya tiene un usuario.",
                status.HTTP_409_CONFLICT,
            )
        except MissingEmailError:
            return validation_error({"email": "La persona no tiene correo; indícalo."})
        except UserAlreadyActiveError:
            return api_error(
                "user_already_active",
                "Ese correo ya tiene una cuenta activa.",
                status.HTTP_409_CONFLICT,
            )
        except UserLinkedToOtherPersonError:
            return api_error(
                "user_linked_to_other_person",
                "Ese correo ya está enlazado con otra persona.",
                status.HTTP_409_CONFLICT,
            )
        except DjangoValidationError:
            return validation_error({"email": "Correo no válido."})
        return invitation_response(result)


# --- Ocupaciones -------------------------------------------------------------


def occupancy_error(exc):
    if isinstance(exc, OccupancyConflictError):
        return api_error("occupancy_conflict", str(exc), status.HTTP_409_CONFLICT)
    return validation_error({"non_field_errors": [str(exc)]}, detail=str(exc))


class UnitOccupanciesView(APIView):
    permission_classes = (IsAdmin,)

    def get(self, request, pk):
        unit = get_object_or_404(Unit, pk=pk)
        queryset = UnitOccupancy.objects.filter(unit=unit).select_related(
            "unit", "person"
        )
        if "active" in request.query_params:
            queryset = queryset.filter(
                end_date__isnull=truthy(request.query_params["active"])
            )
        return paginated(request, queryset, OccupancySerializer, self)

    def post(self, request, pk):
        unit = get_object_or_404(Unit, pk=pk)
        serializer = OccupancyCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        data = serializer.validated_data
        try:
            occupancy = add_occupancy(
                unit,
                data["person"],
                data["relation"],
                start_date=data["start_date"],
                is_primary=data["is_primary"],
                end_date=data["end_date"],
                actor=request.user,
                ip=client_ip(request),
            )
        except (InvalidOccupancyError, OccupancyConflictError) as exc:
            return occupancy_error(exc)
        return Response(
            OccupancySerializer(occupancy).data, status=status.HTTP_201_CREATED
        )


class OccupancyDetailView(APIView):
    permission_classes = (IsAdmin,)

    def patch(self, request, pk):
        occupancy = get_object_or_404(
            UnitOccupancy.objects.select_related("unit", "person"), pk=pk
        )
        serializer = OccupancyUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        data = serializer.validated_data
        try:
            if "end_date" in data:
                close_occupancy(
                    occupancy,
                    data["end_date"],
                    actor=request.user,
                    ip=client_ip(request),
                )
            else:
                make_primary(occupancy, actor=request.user, ip=client_ip(request))
        except (InvalidOccupancyError, OccupancyConflictError) as exc:
            return occupancy_error(exc)
        occupancy.refresh_from_db()
        return Response(OccupancySerializer(occupancy).data)


# --- Roles por torre ---------------------------------------------------------


class RoleAssignmentListCreateView(APIView):
    permission_classes = (IsAdmin,)

    def get(self, request):
        queryset = RoleAssignment.objects.select_related("user", "tower")
        params = request.query_params
        if "tower" in params:
            tower_id = parse_uuid(params["tower"])
            queryset = (
                queryset.filter(tower_id=tower_id) if tower_id else queryset.none()
            )
        if "user" in params:
            user_id = parse_uuid(params["user"])
            queryset = queryset.filter(user_id=user_id) if user_id else queryset.none()
        return paginated(request, queryset, RoleAssignmentSerializer, self)

    def post(self, request):
        serializer = RoleAssignmentSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        data = serializer.validated_data
        try:
            assignment = assign_role(
                data["user"],
                data["role"],
                data["tower"],
                actor=request.user,
                ip=client_ip(request),
            )
        except RoleAlreadyAssignedError:
            return api_error(
                "role_already_assigned",
                "El usuario ya tiene ese rol en esa torre.",
                status.HTTP_409_CONFLICT,
            )
        return Response(
            RoleAssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED
        )


class RoleAssignmentDetailView(APIView):
    permission_classes = (IsAdmin,)

    def delete(self, request, pk):
        assignment = get_object_or_404(
            RoleAssignment.objects.select_related("user", "tower"), pk=pk
        )
        revoke_role(assignment, actor=request.user, ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)
