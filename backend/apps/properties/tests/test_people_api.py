"""S2: personas y su invitación por correo."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Invitation
from apps.core.models import AuditLog
from apps.properties.models import Person
from apps.properties.tests.conftest import PASSWORD, client_for

pytestmark = pytest.mark.django_db

LIST = "/api/v1/people/"


def detail(person):
    return f"/api/v1/people/{person.pk}/"


def invite_url(person):
    return f"/api/v1/people/{person.pk}/invite/"


# --- Permisos ----------------------------------------------------------------


def test_anonimo_no_accede():
    assert APIClient().get(LIST).status_code in (401, 403)


@pytest.mark.parametrize("role", ["BOARD", "TREASURER"])
def test_la_junta_no_gestiona_personas(make_user, give_role, role):
    user = make_user("junta@valleblanco.pe")
    give_role(user, role)
    assert client_for(user).get(LIST).status_code == 403


def test_el_propietario_no_ve_a_las_personas(owner):
    client = client_for(owner)
    assert client.get(LIST).status_code == 403
    assert client.post(LIST, {"full_name": "X"}, format="json").status_code == 403


# --- Crear y editar ----------------------------------------------------------


def test_admin_crea_persona_con_nombres_y_apellidos(admin_client, admin):
    response = admin_client.post(
        LIST,
        {"first_names": "Ana", "last_names": "Torres", "email": "Ana@Mail.com"},
        format="json",
    )

    assert response.status_code == 201
    data = response.json()
    assert data["full_name"] == "Ana Torres"
    assert data["email"] == "ana@mail.com"
    assert data["has_user"] is False
    entry = AuditLog.objects.get(action="PERSON_CREATED")
    assert entry.entity_id == data["id"]
    assert entry.actor_id == str(admin.pk)


def test_correo_y_telefono_son_opcionales(admin_client):
    response = admin_client.post(LIST, {"full_name": "Sin Contacto"}, format="json")
    assert response.status_code == 201
    assert response.json()["email"] == ""
    assert response.json()["phone"] == ""


def test_sin_nombre_devuelve_400(admin_client):
    response = admin_client.post(LIST, {"email": "a@b.pe"}, format="json")
    assert response.status_code == 400
    assert "full_name" in response.json()["errors"]


def test_documento_sin_tipo_devuelve_400(admin_client):
    response = admin_client.post(
        LIST, {"full_name": "Ana", "doc_number": "12345678"}, format="json"
    )
    assert response.status_code == 400
    assert "doc_type" in response.json()["errors"]


def test_documento_repetido_devuelve_400(admin_client, make_person):
    make_person("Existente", doc_type="DNI", doc_number="12345678")

    response = admin_client.post(
        LIST,
        {"full_name": "Otra", "doc_type": "DNI", "doc_number": "12345678"},
        format="json",
    )

    assert response.status_code == 400
    assert "doc_number" in response.json()["errors"]


def test_correo_invalido_devuelve_400(admin_client):
    response = admin_client.post(
        LIST, {"full_name": "Ana", "email": "no-es"}, format="json"
    )
    assert response.status_code == 400
    assert "email" in response.json()["errors"]


def test_admin_completa_correo_y_telefono_despues(admin_client, make_person):
    person = make_person("Ana")

    response = admin_client.patch(
        detail(person), {"email": "Ana@Mail.com", "phone": "999888777"}, format="json"
    )

    assert response.status_code == 200
    person.refresh_from_db()
    assert (person.email, person.phone) == ("ana@mail.com", "999888777")
    entry = AuditLog.objects.get(action="PERSON_UPDATED")
    assert entry.changes == {
        "fields": ["email", "phone"]
    }  # nombres de campo, nunca valores
    assert "999888777" not in str(entry.changes)


def test_editar_conservando_el_propio_documento_no_es_duplicado(
    admin_client, make_person
):
    person = make_person("Ana", doc_type="DNI", doc_number="12345678")
    response = admin_client.patch(detail(person), {"phone": "1"}, format="json")
    assert response.status_code == 200


def test_el_enlace_con_usuario_no_se_edita_por_la_api(
    admin_client, make_person, make_user
):
    person = make_person("Ana")
    user = make_user("otro@valleblanco.pe")

    admin_client.patch(
        detail(person), {"user": str(user.pk), "has_user": True}, format="json"
    )

    person.refresh_from_db()
    assert person.user is None


def test_persona_inexistente_devuelve_404(admin_client):
    url = "/api/v1/people/00000000-0000-4000-8000-000000000000/"
    assert admin_client.get(url).status_code == 404


# --- Listado -----------------------------------------------------------------


def test_listado_con_busqueda_y_filtro_de_usuario(admin_client, make_person, make_user):
    make_person("Ana Torres", email="ana@mail.com")
    make_person("Beto Ruiz", doc_type="DNI", doc_number="87654321")
    make_person("Carla Paz", user=make_user("carla@valleblanco.pe"))

    assert admin_client.get(LIST).json()["count"] == 3
    assert admin_client.get(LIST, {"q": "torres"}).json()["count"] == 1
    assert admin_client.get(LIST, {"q": "8765"}).json()["count"] == 1
    assert admin_client.get(LIST, {"q": "ana@mail"}).json()["count"] == 1
    assert admin_client.get(LIST, {"has_user": "true"}).json()["count"] == 1
    assert admin_client.get(LIST, {"has_user": "false"}).json()["count"] == 2


def test_el_listado_se_pagina_de_a_25(admin_client):
    Person.objects.bulk_create(
        [Person(full_name=f"Persona {n:02d}") for n in range(30)]
    )
    data = admin_client.get(LIST).json()
    assert data["count"] == 30
    assert len(data["results"]) == 25


# --- Invitar -----------------------------------------------------------------


def test_invitar_a_una_persona_enlaza_la_cuenta(admin_client, make_person):
    person = make_person("Ana", email="ana@valleblanco.pe")

    response = admin_client.post(invite_url(person))

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "ana@valleblanco.pe"
    assert data["invitation"]["path"] == f"/invitacion/{data['invitation']['token']}"
    assert response["Cache-Control"] == "no-store"
    person.refresh_from_db()
    assert person.user is not None
    assert person.user.is_active is False
    assert admin_client.get(detail(person)).json()["has_user"] is True


def test_invitar_con_un_correo_distinto_y_sin_correo_previo(admin_client, make_person):
    person = make_person("Ana")

    response = admin_client.post(
        invite_url(person), {"email": "ana@valleblanco.pe"}, format="json"
    )

    assert response.status_code == 201
    person.refresh_from_db()
    assert person.email == "ana@valleblanco.pe"


def test_invitar_sin_correo_devuelve_400(admin_client, make_person):
    response = admin_client.post(invite_url(make_person("Ana")))
    assert response.status_code == 400
    assert "email" in response.json()["errors"]


def test_invitar_con_correo_invalido_devuelve_400(admin_client, make_person):
    response = admin_client.post(
        invite_url(make_person("Ana")), {"email": "no-es"}, format="json"
    )
    assert response.status_code == 400


def test_invitar_dos_veces_a_la_misma_persona_devuelve_409(admin_client, make_person):
    person = make_person("Ana", email="ana@valleblanco.pe")
    admin_client.post(invite_url(person))

    response = admin_client.post(invite_url(person))

    assert response.status_code == 409
    assert response.json()["code"] == "person_already_linked"


def test_invitar_a_un_correo_con_cuenta_activa_devuelve_409(
    admin_client, make_person, make_user
):
    make_user("activo@valleblanco.pe")
    response = admin_client.post(
        invite_url(make_person("Ana")),
        {"email": "activo@valleblanco.pe"},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "user_already_active"


def test_correo_de_un_usuario_enlazado_a_otra_persona_devuelve_409_sin_efectos(
    admin_client, make_person
):
    admin_client.post(
        invite_url(make_person("Uno")), {"email": "a@valleblanco.pe"}, format="json"
    )
    invitations = Invitation.objects.count()

    response = admin_client.post(
        invite_url(make_person("Dos")), {"email": "a@valleblanco.pe"}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "user_linked_to_other_person"
    assert Invitation.objects.count() == invitations


def test_solo_la_administracion_puede_invitar_personas(owner, make_person):
    person = make_person("Ana", email="ana@valleblanco.pe")
    assert client_for(owner).post(invite_url(person)).status_code == 403


def test_invitar_a_una_persona_inexistente_devuelve_404(admin_client):
    url = "/api/v1/people/00000000-0000-4000-8000-000000000000/invite/"
    assert admin_client.post(url).status_code == 404


def test_flujo_completo_invitar_aceptar_entrar_y_ver_mis_unidades(
    admin_client, unit, make_person, make_occupancy
):
    person = make_person("Ana Torres", email="ana@valleblanco.pe")
    make_occupancy(unit, person, "OWNER", is_primary=True)
    token = admin_client.post(invite_url(person)).json()["invitation"]["token"]

    anon = APIClient()
    accepted = anon.post(
        "/api/v1/auth/invitations/accept/",
        {"token": token, "password": PASSWORD},
        format="json",
    )
    login = anon.post(
        "/api/v1/auth/login/",
        {"email": "ana@valleblanco.pe", "password": PASSWORD},
        format="json",
    )
    me = anon.get("/api/v1/auth/me/").json()

    assert accepted.status_code == 200
    assert login.status_code == 200
    assert me["roles"] == ["OWNER"]
    assert [u["code"] for u in me["units"]] == ["101"]
    assert anon.get("/api/v1/units/").json()["count"] == 1
