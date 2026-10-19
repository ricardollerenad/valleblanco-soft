"""T4: límite de intentos de login por cuenta y por IP, e IP del cliente."""

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rest_framework.test import APIClient

from apps.accounts.security import client_ip

pytestmark = pytest.mark.django_db

User = get_user_model()
LOGIN = "/api/v1/auth/login/"
EMAIL = "vecino@valleblanco.pe"
STRONG = "Cielo-Azul-9347-Zq"
WRONG = "clave-equivocada-9347"


def attempt(client, email=EMAIL, password=WRONG, **extra):
    return client.post(
        LOGIN, {"email": email, "password": password}, format="json", **extra
    )


def fail_times(client, times, email=EMAIL):
    for _ in range(times):
        assert attempt(client, email=email).status_code == 400


@pytest.fixture
def vecino():
    return User.objects.create_user(EMAIL, STRONG)


def test_cinco_fallos_bloquean_la_cuenta_incluso_con_la_clave_correcta(vecino):
    client = APIClient()
    fail_times(client, 5)

    response = attempt(client, password=STRONG)

    assert response.status_code == 429
    assert response.json()["code"] == "too_many_attempts"
    assert int(response["Retry-After"]) > 0


def test_cuatro_fallos_todavia_no_bloquean(vecino):
    client = APIClient()
    fail_times(client, 4)

    assert attempt(client, password=STRONG).status_code == 200


def test_bloquear_una_cuenta_no_afecta_a_otra(vecino):
    User.objects.create_user("otro@valleblanco.pe", STRONG)
    client = APIClient()
    fail_times(client, 5)

    response = attempt(client, email="otro@valleblanco.pe", password=STRONG)

    assert response.status_code == 200


def test_el_bloqueo_no_distingue_mayusculas_del_correo(vecino):
    client = APIClient()
    fail_times(client, 5, email="VECINO@ValleBlanco.PE")

    assert attempt(client, password=STRONG).status_code == 429


def test_el_bloqueo_tambien_aplica_a_correos_inexistentes():
    client = APIClient()
    fail_times(client, 5, email="nadie@valleblanco.pe")

    response = attempt(client, email="nadie@valleblanco.pe")

    assert response.status_code == 429


def test_un_login_correcto_reinicia_el_contador(vecino):
    client = APIClient()
    fail_times(client, 4)
    assert attempt(client, password=STRONG).status_code == 200

    fail_times(client, 4)

    assert attempt(client, password=STRONG).status_code == 200


def test_veinte_fallos_desde_una_ip_la_bloquean(vecino):
    client = APIClient()
    for index in range(20):
        response = attempt(client, email=f"intruso{index}@valleblanco.pe")
        assert response.status_code == 400

    assert attempt(client, password=STRONG).status_code == 429


def test_rotar_la_cabecera_x_forwarded_for_no_evita_el_bloqueo(vecino, settings):
    settings.TRUSTED_PROXY_COUNT = 1
    client = APIClient()
    for index in range(20):
        response = attempt(
            client,
            email=f"intruso{index}@valleblanco.pe",
            HTTP_X_FORWARDED_FOR=f"10.0.0.{index}, 203.0.113.9",
        )
        assert response.status_code == 400

    response = attempt(
        client, password=STRONG, HTTP_X_FORWARDED_FOR="10.9.9.9, 203.0.113.9"
    )

    assert response.status_code == 429


# --- client_ip -----------------------------------------------------------


def test_client_ip_usa_remote_addr_por_defecto():
    request = RequestFactory().get(
        "/", REMOTE_ADDR="203.0.113.7", HTTP_X_FORWARDED_FOR="1.2.3.4"
    )

    assert client_ip(request) == "203.0.113.7"


def test_client_ip_con_un_proxy_de_confianza_usa_la_ultima_entrada(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    request = RequestFactory().get(
        "/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4, 198.51.100.5"
    )

    assert client_ip(request) == "198.51.100.5"


def test_client_ip_con_proxy_pero_sin_cabecera_vuelve_a_remote_addr(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    request = RequestFactory().get("/", REMOTE_ADDR="203.0.113.7")

    assert client_ip(request) == "203.0.113.7"
