"""S2: restricciones del modelo de propiedades (se exigen en la base de datos)."""

from datetime import date

import pytest
from django.db import IntegrityError, transaction

from apps.properties.models import (
    Person,
    RoleAssignment,
    Tower,
    Unit,
    UnitOccupancy,
)

pytestmark = pytest.mark.django_db


def test_el_codigo_de_unidad_es_unico_por_torre(tower, unit):
    with pytest.raises(IntegrityError), transaction.atomic():
        Unit.objects.create(tower=tower, code="101", floor=1, number=1)


def test_el_mismo_codigo_se_puede_usar_en_otra_torre(tower, unit):
    other = Tower.objects.create(
        condominium=tower.condominium, name="Torre 3", floors=5
    )
    Unit.objects.create(tower=other, code="101", floor=1, number=1)
    assert Unit.objects.filter(code="101").count() == 2


def test_nombre_de_torre_unico_por_condominio(tower):
    with pytest.raises(IntegrityError), transaction.atomic():
        Tower.objects.create(condominium=tower.condominium, name="Torre 2", floors=3)


def test_persona_arma_el_nombre_completo_y_normaliza_el_correo():
    person = Person.objects.create(
        first_names="Ana", last_names="Torres", email="  Ana@Mail.COM "
    )
    assert person.full_name == "Ana Torres"
    assert person.email == "ana@mail.com"


def test_documento_unico_cuando_esta_informado():
    Person.objects.create(full_name="A", doc_type="DNI", doc_number="12345678")
    with pytest.raises(IntegrityError), transaction.atomic():
        Person.objects.create(full_name="B", doc_type="DNI", doc_number="12345678")


def test_varias_personas_pueden_no_tener_documento():
    Person.objects.create(full_name="A")
    Person.objects.create(full_name="B")
    assert Person.objects.count() == 2


def test_un_usuario_solo_puede_enlazarse_a_una_persona(make_user, make_person):
    user = make_user("a@valleblanco.pe")
    make_person("Uno", user=user)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_person("Dos", user=user)


def test_solo_un_titular_principal_vigente_por_unidad(
    unit, make_person, make_occupancy
):
    make_occupancy(unit, make_person("Uno"), is_primary=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_occupancy(unit, make_person("Dos"), is_primary=True)


def test_un_titular_cerrado_no_bloquea_al_nuevo(unit, make_person, make_occupancy):
    make_occupancy(unit, make_person("Uno"), is_primary=True, end_date=date(2025, 1, 1))
    make_occupancy(unit, make_person("Dos"), is_primary=True)
    assert unit.occupancies.filter(is_primary=True).count() == 2


def test_un_inquilino_no_puede_ser_titular_principal(unit, make_person, make_occupancy):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_occupancy(unit, make_person("Inq"), "TENANT", is_primary=True)


def test_la_fecha_final_no_puede_ser_anterior_al_inicio(
    unit, make_person, make_occupancy
):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_occupancy(
            unit,
            make_person("Uno"),
            start_date=date(2025, 5, 1),
            end_date=date(2025, 4, 1),
        )


def test_no_se_repite_la_misma_relacion_vigente(unit, make_person, make_occupancy):
    person = make_person("Uno")
    make_occupancy(unit, person, "OWNER")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_occupancy(unit, person, "OWNER")


def test_una_persona_puede_ser_propietaria_e_inquilina_a_la_vez(
    unit, unit2, make_person, make_occupancy
):
    person = make_person("Uno")
    make_occupancy(unit, person, "OWNER")
    make_occupancy(unit2, person, "TENANT")
    assert UnitOccupancy.objects.filter(person=person).count() == 2


def test_un_rol_no_se_repite_por_usuario_y_torre(tower, make_user):
    user = make_user("junta@valleblanco.pe")
    RoleAssignment.objects.create(user=user, role="BOARD", tower=tower)
    with pytest.raises(IntegrityError), transaction.atomic():
        RoleAssignment.objects.create(user=user, role="BOARD", tower=tower)
