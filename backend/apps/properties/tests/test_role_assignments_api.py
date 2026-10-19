"""S2: asignación de roles de gestión por torre."""

import pytest

from apps.core.models import AuditLog
from apps.properties.models import RoleAssignment, Tower
from apps.properties.tests.conftest import client_for

pytestmark = pytest.mark.django_db

URL = "/api/v1/role-assignments/"
MISSING = "00000000-0000-4000-8000-000000000000"


def test_admin_asigna_un_rol(admin_client, admin, tower, make_user):
    user = make_user("junta@valleblanco.pe")

    response = admin_client.post(
        URL,
        {"user": str(user.pk), "role": "BOARD", "tower": str(tower.pk)},
        format="json",
    )

    assert response.status_code == 201
    data = response.json()
    assert (data["user_email"], data["role"]) == ("junta@valleblanco.pe", "BOARD")
    entry = AuditLog.objects.get(action="ROLE_ASSIGNED")
    assert entry.actor_id == str(admin.pk)
    assert entry.ip_address == "127.0.0.1"


def test_el_rol_asignado_aparece_en_el_perfil_del_usuario(
    admin_client, tower, make_user
):
    user = make_user("tesorero@valleblanco.pe")
    admin_client.post(
        URL,
        {"user": str(user.pk), "role": "TREASURER", "tower": str(tower.pk)},
        format="json",
    )

    assert client_for(user).get("/api/v1/auth/me/").json()["roles"] == [
        "BOARD",
        "TREASURER",
    ]


def test_repetir_el_rol_devuelve_409(admin_client, tower, make_user, give_role):
    user = make_user("junta@valleblanco.pe")
    give_role(user, "BOARD")

    response = admin_client.post(
        URL,
        {"user": str(user.pk), "role": "BOARD", "tower": str(tower.pk)},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["code"] == "role_already_assigned"


@pytest.mark.parametrize(
    "body_overrides, field",
    [
        ({"role": "OWNER"}, "role"),
        ({"role": "SUPER"}, "role"),
        ({"user": MISSING}, "user"),
        ({"tower": MISSING}, "tower"),
    ],
)
def test_datos_invalidos_devuelven_400(
    admin_client, tower, make_user, body_overrides, field
):
    user = make_user("junta@valleblanco.pe")
    body = {
        "user": str(user.pk),
        "role": "BOARD",
        "tower": str(tower.pk),
        **body_overrides,
    }

    response = admin_client.post(URL, body, format="json")

    assert response.status_code == 400
    assert field in response.json()["errors"]
    assert RoleAssignment.objects.count() == 0


def test_solo_el_admin_gestiona_roles(owner, tower, make_user, give_role):
    target = make_user("otro@valleblanco.pe")
    junta = make_user("junta@valleblanco.pe")
    give_role(junta, "BOARD")
    body = {"user": str(target.pk), "role": "BOARD", "tower": str(tower.pk)}

    for user in (owner, junta):
        client = client_for(user)
        assert client.get(URL).status_code == 403
        assert client.post(URL, body, format="json").status_code == 403
    assert RoleAssignment.objects.filter(user=target).count() == 0


def test_no_se_puede_auto_asignar_admin_sin_ser_admin(owner, tower):
    response = client_for(owner).post(
        URL,
        {"user": str(owner.pk), "role": "ADMIN", "tower": str(tower.pk)},
        format="json",
    )
    assert response.status_code == 403
    assert "ADMIN" not in client_for(owner).get("/api/v1/auth/me/").json()["roles"]


def test_listado_con_filtros(admin_client, tower, make_user, give_role):
    other_tower = Tower.objects.create(
        condominium=tower.condominium, name="Torre 3", floors=2
    )
    one, two = make_user("uno@valleblanco.pe"), make_user("dos@valleblanco.pe")
    give_role(one, "BOARD")
    RoleAssignment.objects.create(user=two, role="BOARD", tower=other_tower)

    assert admin_client.get(URL).json()["count"] == 2
    assert admin_client.get(URL, {"tower": str(other_tower.pk)}).json()["count"] == 1
    assert admin_client.get(URL, {"user": str(one.pk)}).json()["count"] == 1
    assert admin_client.get(URL, {"user": "basura"}).json()["count"] == 0


def test_revocar_un_rol(admin_client, make_user, give_role):
    user = make_user("junta@valleblanco.pe")
    assignment = give_role(user, "BOARD")

    response = admin_client.delete(f"{URL}{assignment.pk}/")

    assert response.status_code == 204
    assert RoleAssignment.objects.count() == 0
    assert AuditLog.objects.filter(action="ROLE_REVOKED").count() == 1
    assert client_for(user).get("/api/v1/auth/me/").json()["roles"] == []


def test_revocar_un_rol_inexistente_devuelve_404(admin_client):
    assert admin_client.delete(f"{URL}{MISSING}/").status_code == 404


def test_solo_el_admin_revoca(owner, make_user, give_role):
    assignment = give_role(make_user("junta@valleblanco.pe"), "BOARD")
    assert client_for(owner).delete(f"{URL}{assignment.pk}/").status_code == 403
    assert RoleAssignment.objects.count() == 1
