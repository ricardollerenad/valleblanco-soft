"""Vistas de sesión: csrf, login, logout y perfil."""

from django.contrib.auth import authenticate, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .security import (
    clear_failures,
    client_ip,
    is_blocked,
    lockout_seconds,
    register_failure,
)
from .serializers import LoginSerializer


def api_error(code, detail, http_status, errors=None):
    return Response(
        {"code": code, "detail": detail, "errors": errors or {}}, status=http_status
    )


def profile_payload(user):
    # Los roles por torre y las unidades llegan en S2; mientras tanto el
    # superusuario cuenta como administrador.
    return {
        "id": str(user.pk),
        "email": user.email,
        "roles": ["ADMIN"] if user.is_superuser else [],
        "units": [],
    }


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
            return api_error(
                "invalid_credentials",
                "Correo o contraseña incorrectos.",
                status.HTTP_400_BAD_REQUEST,
            )

        clear_failures(email)
        login(request, user)
        return Response(profile_payload(user))


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(profile_payload(request.user))
