"""S1 · T4 — API de sesión: csrf, login, logout y me."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()

pytestmark = pytest.mark.django_db

CSRF = "/api/v1/auth/csrf/"
LOGIN = "/api/v1/auth/login/"
LOGOUT = "/api/v1/auth/logout/"
ME = "/api/v1/auth/me/"
CLAVE = "Cerro-Blanco-2026"


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def vecino():
    return User.objects.create_user("vecino@example.com", CLAVE)


def _login(client, email="vecino@example.com", password=CLAVE):
    return client.post(LOGIN, {"email": email, "password": password}, format="json")


def test_csrf_entrega_la_cookie(client):
    response = client.get(CSRF)
    assert response.status_code == 200
    assert "csrftoken" in response.cookies


def test_login_correcto_abre_sesion(client, vecino):
    response = _login(client)
    assert response.status_code == 200
    assert response.json()["email"] == "vecino@example.com"
    assert client.get(ME).status_code == 200


def test_login_ignora_mayusculas_y_espacios_en_el_correo(client, vecino):
    assert _login(client, email="  VECINO@Example.COM ").status_code == 200


def test_login_con_clave_incorrecta_falla(client, vecino):
    response = _login(client, password="clave-equivocada-1")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_credentials"
    assert client.get(ME).status_code == 403


def test_login_no_revela_si_el_correo_existe(client, vecino):
    existente = _login(client, password="clave-equivocada-1").json()
    inexistente = _login(client, email="nadie@example.com").json()
    assert existente == inexistente


def test_login_de_usuario_pendiente_de_invitacion_falla(client):
    User.objects.create_user("pendiente@example.com", password=None, is_active=False)
    response = _login(client, email="pendiente@example.com", password=CLAVE)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_credentials"


def test_login_sin_datos_devuelve_errores_por_campo(client):
    response = client.post(LOGIN, {}, format="json")
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "validation_error"
    assert {"email", "password"} <= set(body["errors"])


def test_login_exige_post(client):
    assert client.get(LOGIN).status_code == 405


def test_login_exige_csrf_cuando_esta_activo(vecino):
    client = APIClient(enforce_csrf_checks=True)
    datos = {"email": "vecino@example.com", "password": CLAVE}
    assert client.post(LOGIN, datos, format="json").status_code == 403

    client.get(CSRF)
    token = client.cookies["csrftoken"].value
    response = client.post(LOGIN, datos, format="json", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200


def test_me_sin_sesion_se_rechaza(client):
    assert client.get(ME).status_code == 403


def test_me_de_un_vecino_devuelve_su_perfil(client, vecino):
    _login(client)
    body = client.get(ME).json()
    assert body["id"] == str(vecino.pk)
    assert body["email"] == "vecino@example.com"
    assert body["roles"] == []
    assert body["units"] == []


def test_me_de_un_superusuario_incluye_el_rol_admin(client):
    User.objects.create_superuser("root@example.com", CLAVE)
    _login(client, email="root@example.com")
    assert client.get(ME).json()["roles"] == ["ADMIN"]


def test_logout_cierra_la_sesion(client, vecino):
    _login(client)
    assert client.post(LOGOUT).status_code == 204
    assert client.get(ME).status_code == 403


def test_logout_sin_sesion_se_rechaza(client):
    assert client.post(LOGOUT).status_code == 403
