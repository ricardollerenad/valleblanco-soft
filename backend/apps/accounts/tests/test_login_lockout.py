"""S1 · T4 — Límite de intentos de login, por cuenta y por IP."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APIRequestFactory

from apps.accounts.security import client_ip

User = get_user_model()

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
CLAVE = "Cerro-Blanco-2026"


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def vecino():
    return User.objects.create_user("vecino@example.com", CLAVE)


def _intento(client, email="vecino@example.com", password="mala-clave-123", **extra):
    return client.post(
        LOGIN, {"email": email, "password": password}, format="json", **extra
    )


def test_cinco_fallos_bloquean_la_cuenta_incluso_con_la_clave_correcta(client, vecino):
    for _ in range(5):
        assert _intento(client).status_code == 400
    response = _intento(client, password=CLAVE)
    assert response.status_code == 429
    assert response.json()["code"] == "too_many_attempts"
    assert int(response["Retry-After"]) > 0


def test_el_bloqueo_no_distingue_mayusculas_del_correo(client, vecino):
    for _ in range(5):
        _intento(client, email="VECINO@example.com")
    assert (
        _intento(client, email="vecino@example.com", password=CLAVE).status_code == 429
    )


def test_un_login_correcto_reinicia_el_contador(client, vecino):
    for _ in range(4):
        _intento(client)
    assert _intento(client, password=CLAVE).status_code == 200
    for _ in range(4):
        assert _intento(client).status_code == 400


def test_el_bloqueo_tambien_aplica_a_correos_inexistentes(client):
    for _ in range(5):
        _intento(client, email="fantasma@example.com")
    assert _intento(client, email="fantasma@example.com").status_code == 429


def test_bloquear_una_cuenta_no_afecta_a_otra(client, vecino):
    otro = User.objects.create_user("otro@example.com", CLAVE)
    for _ in range(5):
        _intento(client)
    assert _intento(client, email=otro.email, password=CLAVE).status_code == 200


def test_veinte_fallos_desde_una_ip_la_bloquean(client):
    for i in range(20):
        assert _intento(client, email=f"user{i}@example.com").status_code == 400
    assert _intento(client, email="nuevo@example.com").status_code == 429


def test_rotar_la_cabecera_x_forwarded_for_no_evita_el_bloqueo(client):
    for i in range(20):
        _intento(
            client, email=f"user{i}@example.com", HTTP_X_FORWARDED_FOR=f"1.1.1.{i}"
        )
    response = _intento(
        client, email="nuevo@example.com", HTTP_X_FORWARDED_FOR="9.9.9.9"
    )
    assert response.status_code == 429


def test_client_ip_usa_remote_addr_por_defecto():
    request = APIRequestFactory().get(
        "/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4"
    )
    assert client_ip(request) == "10.0.0.1"


def test_client_ip_con_un_proxy_de_confianza_usa_la_ultima_entrada(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    request = APIRequestFactory().get(
        "/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8"
    )
    assert client_ip(request) == "5.6.7.8"


def test_client_ip_con_proxy_pero_sin_cabecera_vuelve_a_remote_addr(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    request = APIRequestFactory().get("/", REMOTE_ADDR="127.0.0.1")
    assert client_ip(request) == "127.0.0.1"
