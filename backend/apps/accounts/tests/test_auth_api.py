"""T4: API de sesión (csrf, login, logout y perfil)."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.services import invite_user

pytestmark = pytest.mark.django_db

User = get_user_model()
CSRF = "/api/v1/auth/csrf/"
LOGIN = "/api/v1/auth/login/"
LOGOUT = "/api/v1/auth/logout/"
ME = "/api/v1/auth/me/"
EMAIL = "vecino@valleblanco.pe"
STRONG = "Cielo-Azul-9347-Zq"


@pytest.fixture
def vecino():
    return User.objects.create_user(EMAIL, STRONG)


def login(client, email=EMAIL, password=STRONG):
    return client.post(LOGIN, {"email": email, "password": password}, format="json")


# --- CSRF ----------------------------------------------------------------


def test_csrf_entrega_la_cookie():
    response = APIClient().get(CSRF)

    assert response.status_code == 200
    assert "csrftoken" in response.cookies


def test_login_exige_csrf_cuando_esta_activo(vecino):
    client = APIClient(enforce_csrf_checks=True)

    response = login(client)

    assert response.status_code == 403


def test_login_exige_post():
    assert APIClient().get(LOGIN).status_code == 405


# --- Login ---------------------------------------------------------------


def test_login_correcto_abre_sesion(vecino):
    client = APIClient()

    response = login(client)

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL
    assert client.get(ME).status_code == 200


def test_login_con_clave_incorrecta_falla(vecino):
    response = login(APIClient(), password="otra-clave-9347")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_credentials"


def test_login_de_usuario_pendiente_de_invitacion_falla():
    invite_user(EMAIL)

    response = login(APIClient(), password=STRONG)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_credentials"


def test_login_ignora_mayusculas_y_espacios_en_el_correo(vecino):
    response = login(APIClient(), email="  Vecino@ValleBlanco.PE  ")

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


def test_login_no_revela_si_el_correo_existe(vecino):
    existente = login(APIClient(), password="otra-clave-9347")
    inexistente = login(APIClient(), email="nadie@valleblanco.pe", password="x-9347")

    assert existente.status_code == inexistente.status_code == 400
    assert existente.json() == inexistente.json()


def test_login_sin_datos_devuelve_errores_por_campo():
    response = APIClient().post(LOGIN, {}, format="json")

    body = response.json()
    assert response.status_code == 400
    assert body["code"] == "validation_error"
    assert "email" in body["errors"]
    assert "password" in body["errors"]


# --- Logout --------------------------------------------------------------


def test_logout_cierra_la_sesion(vecino):
    client = APIClient()
    login(client)

    response = client.post(LOGOUT)

    assert response.status_code == 204
    assert client.get(ME).status_code in (401, 403)


def test_logout_sin_sesion_se_rechaza():
    assert APIClient().post(LOGOUT).status_code in (401, 403)


# --- Perfil --------------------------------------------------------------


def test_me_sin_sesion_se_rechaza():
    assert APIClient().get(ME).status_code in (401, 403)


def test_me_de_un_vecino_devuelve_su_perfil(vecino):
    client = APIClient()
    client.force_authenticate(vecino)

    body = client.get(ME).json()

    assert body == {"id": str(vecino.pk), "email": EMAIL, "roles": [], "units": []}


def test_me_de_un_superusuario_incluye_el_rol_admin():
    admin = User.objects.create_superuser("admin@valleblanco.pe", STRONG)
    client = APIClient()
    client.force_authenticate(admin)

    assert client.get(ME).json()["roles"] == ["ADMIN"]
