"""S2: /auth/me, el login y el permiso de administrador usan los roles de S2."""

from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.properties.tests.conftest import PASSWORD, client_for

pytestmark = pytest.mark.django_db

ME = "/api/v1/auth/me/"
LOGIN = "/api/v1/auth/login/"
INVITE = "/api/v1/users/"


def test_el_propietario_ve_su_rol_y_sus_unidades(owner, unit):
    data = client_for(owner).get(ME).json()

    assert data["roles"] == ["OWNER"]
    assert data["units"] == [
        {"id": str(unit.pk), "code": "101", "relation": "OWNER", "is_primary": True}
    ]


def test_el_inquilino_ve_su_rol_de_inquilino(
    unit, make_user, make_person, make_occupancy
):
    user = make_user("inq@valleblanco.pe")
    make_occupancy(unit, make_person("Inq", user=user), "TENANT")

    data = client_for(user).get(ME).json()

    assert data["roles"] == ["TENANT"]
    assert data["units"][0]["relation"] == "TENANT"
    assert data["units"][0]["is_primary"] is False


def test_una_ocupacion_cerrada_ya_no_da_rol_ni_unidad(
    unit, make_user, make_person, make_occupancy
):
    user = make_user("ex@valleblanco.pe")
    make_occupancy(
        unit, make_person("Ex", user=user), "OWNER", end_date=date(2025, 1, 1)
    )

    data = client_for(user).get(ME).json()

    assert data["roles"] == []
    assert data["units"] == []


def test_varias_unidades_aparecen_ordenadas(
    unit, unit2, make_user, make_person, make_occupancy
):
    user = make_user("dos@valleblanco.pe")
    person = make_person("Dos", user=user)
    make_occupancy(unit2, person, "OWNER")
    make_occupancy(unit, person, "OWNER")

    data = client_for(user).get(ME).json()

    assert [u["code"] for u in data["units"]] == ["101", "102"]
    assert data["roles"] == ["OWNER"]


def test_el_tesorero_cuenta_tambien_como_junta(make_user, give_role):
    user = make_user("tesorero@valleblanco.pe")
    give_role(user, "TREASURER")

    assert client_for(user).get(ME).json()["roles"] == ["BOARD", "TREASURER"]


def test_un_administrador_por_torre_aparece_como_admin(make_user, give_role):
    user = make_user("admintorre@valleblanco.pe")
    give_role(user, "ADMIN")

    assert client_for(user).get(ME).json()["roles"] == ["ADMIN"]


def test_el_superusuario_sigue_siendo_admin(admin_client):
    assert admin_client.get(ME).json()["roles"] == ["ADMIN"]


def test_el_login_devuelve_los_mismos_roles_y_unidades(owner):
    response = APIClient().post(
        LOGIN, {"email": "duenio@valleblanco.pe", "password": PASSWORD}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["roles"] == ["OWNER"]
    assert response.json()["units"][0]["code"] == "101"


def test_un_admin_por_rol_puede_invitar_usuarios(make_user, give_role):
    user = make_user("admintorre@valleblanco.pe")
    give_role(user, "ADMIN")

    response = client_for(user).post(INVITE, {"email": "nuevo@valleblanco.pe"})

    assert response.status_code == 201


@pytest.mark.parametrize("role", ["BOARD", "TREASURER"])
def test_la_junta_no_puede_invitar_usuarios(make_user, give_role, role):
    user = make_user("junta@valleblanco.pe")
    give_role(user, role)

    response = client_for(user).post(INVITE, {"email": "nuevo@valleblanco.pe"})

    assert response.status_code == 403


def test_un_propietario_no_puede_invitar_usuarios(owner):
    response = client_for(owner).post(INVITE, {"email": "nuevo@valleblanco.pe"})
    assert response.status_code == 403
