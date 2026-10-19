"""Vistas de sesión: csrf, login, logout y perfil."""

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import log_event
from apps.core.profile import collect_profile

from .permissions import IsAdmin
from .security import (
    clear_failures,
    client_ip,
    is_blocked,
    lockout_seconds,
    register_failure,
    should_log_block,
)
from .serializers import (
    AcceptInvitationSerializer,
    InviteUserSerializer,
    LoginSerializer,
)
from .services import (
    InvitationAlreadyUsedError,
    InvitationExpiredError,
    InvitationNotFoundError,
    UserAlreadyActiveError,
    accept_invitation,
    invite_user,
)


def api_error(code, detail, http_status, errors=None):
    return Response(
        {"code": code, "detail": detail, "errors": errors or {}}, status=http_status
    )


def profile_payload(user):
    # Los roles y unidades los aportan las apps que los conocen (`core.profile`).
    roles, units = collect_profile(user)
    return {"id": str(user.pk), "email": user.email, "roles": roles, "units": units}


def invitation_response(result):
    """Respuesta 201 con el enlace de invitación. Solo la administración la recibe."""
    response = Response(
        {
            "id": str(result.user.pk),
            "email": result.user.email,
            "invitation": {
                "token": result.token,
                "path": f"/invitacion/{result.token}",
                "expires_at": result.invitation.expires_at,
            },
        },
        status=status.HTTP_201_CREATED,
    )
    response["Cache-Control"] = "no-store"
    return response


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    def get(self, request):
        return Response({"detail": "ok"})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                "validation_error",
                "Revisa los datos ingresados.",
                status.HTTP_400_BAD_REQUEST,
                serializer.errors,
            )
        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]
        ip = client_ip(request)

        if is_blocked(email, ip):
            if should_log_block(email):
                log_event("LOGIN_BLOCKED", "User", "", actor_label=email, ip=ip)
            response = api_error(
                "too_many_attempts",
                "Demasiados intentos. Espera unos minutos antes de volver a intentar.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = str(lockout_seconds())
            return response

        user = authenticate(request, username=email, password=password)
        if user is None:
            register_failure(email, ip)
            log_event("LOGIN_FAILED", "User", "", actor_label=email, ip=ip)
            return api_error(
                "invalid_credentials",
                "Correo o contraseña incorrectos.",
                status.HTTP_400_BAD_REQUEST,
            )

        clear_failures(email)
        login(request, user)
        log_event("LOGIN_SUCCEEDED", "User", user.pk, actor=user, ip=ip)
        return Response(profile_payload(user))


class LogoutView(APIView):
    def post(self, request):
        user = request.user
        logout(request)
        log_event("LOGOUT", "User", user.pk, actor=user, ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(profile_payload(request.user))


class InviteUserView(APIView):
    """POST /users/: la administración crea e invita. El enlace lo ve solo ella."""

    permission_classes = (IsAdmin,)

    def post(self, request):
        serializer = InviteUserSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                "validation_error",
                "Revisa los datos ingresados.",
                status.HTTP_400_BAD_REQUEST,
                serializer.errors,
            )
        try:
            result = invite_user(
                serializer.validated_data["email"],
                invited_by=request.user,
                ip=client_ip(request),
            )
        except UserAlreadyActiveError:
            return api_error(
                "user_already_active",
                "Ese correo ya tiene una cuenta activa.",
                status.HTTP_409_CONFLICT,
            )

        return invitation_response(result)


class AcceptInvitationView(APIView):
    """POST /auth/invitations/accept/: público; el token es el secreto."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                "validation_error",
                "Revisa los datos ingresados.",
                status.HTTP_400_BAD_REQUEST,
                serializer.errors,
            )
        try:
            accept_invitation(
                serializer.validated_data["token"],
                serializer.validated_data["password"],
                ip=client_ip(request),
            )
        except InvitationAlreadyUsedError:
            return api_error(
                "invitation_used",
                "Esta invitación ya fue utilizada.",
                status.HTTP_400_BAD_REQUEST,
            )
        except InvitationExpiredError:
            return api_error(
                "invitation_expired",
                "La invitación venció. Pide a la administración una nueva.",
                status.HTTP_400_BAD_REQUEST,
            )
        except InvitationNotFoundError:
            return api_error(
                "invitation_invalid",
                "El enlace de invitación no es válido.",
                status.HTTP_400_BAD_REQUEST,
            )
        except DjangoValidationError as exc:
            return api_error(
                "validation_error",
                "La contraseña no cumple los requisitos.",
                status.HTTP_400_BAD_REQUEST,
                {"password": exc.messages},
            )
        return Response({"detail": "ok"})
