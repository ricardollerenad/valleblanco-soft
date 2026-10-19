"""S3-B: cargos (GET/POST /charges/) con aislamiento entre unidades."""

from datetime import date
from decimal import Decimal

import pytest

from apps.core.models import AuditLog
from apps.finances import services
from apps.finances.models import Charge

pytestmark = pytest.mark.django_db

URL = "/api/v1/charges/"
INEXISTENTE = "00000000-0000-4000-8000-000000000000"


def meses(inicio, cantidad):
    year, month = inicio.year, inicio.month
    for _ in range(cantidad):
        yield date(year, month, 1)
        month += 1
        if month == 13:
            year, month = year + 1, 1


# --- Listado -----------------------------------------------------------------


def test_anonimo_no_puede_listar_cargos(actores):
    assert actores["anonimo"].get(URL).status_code in (401, 403)


@pytest.mark.parametrize("rol", ["admin", "admin_rol", "junta", "tesorero"])
def test_la_gestion_ve_los_cargos_de_todas_las_unidades(
    actores, rol, unit, unit2, make_charge
):
    make_charge(unit, date(2025, 3, 1))
    make_charge(unit2, date(2025, 3, 1))

    data = actores[rol].get(URL).json()

    assert data["count"] == 2


def test_el_listado_se_pagina_de_a_25(admin_client, unit, make_charge):
    for periodo in meses(date(2023, 1, 1), 26):
        make_charge(unit, periodo)

    data = admin_client.get(URL).json()

    assert data["count"] == 26
    assert len(data["results"]) == 25
    assert len(admin_client.get(URL, {"page": 2}).json()["results"]) == 1


def test_el_cargo_se_serializa_con_dinero_como_texto(admin_client, unit, make_charge):
    make_charge(unit, date(2025, 3, 1), description="Cuota ordinaria 03/2025")

    cargo = admin_client.get(URL).json()["results"][0]

    assert cargo["unit"] == str(unit.pk)
    assert cargo["unit_code"] == "101"
    assert cargo["concept"] == "ORDINARY_FEE"
    assert cargo["period"] == "2025-03-01"
    assert cargo["description"] == "Cuota ordinaria 03/2025"
    assert cargo["amount"] == "80.00"
    assert cargo["pending_amount"] == "80.00"
    assert cargo["due_date"] == "2025-03-28"
    assert cargo["status"] == "OPEN"
    assert cargo["source"] == "SYSTEM"


def test_el_pendiente_descuenta_lo_aplicado(
    admin_client, unit, make_charge, monkeypatch
):
    cargo = make_charge(unit, date(2025, 3, 1))
    monkeypatch.setattr(
        services, "allocated_totals", lambda ids: {cargo.pk: Decimal("30.00")}
    )

    resultado = admin_client.get(URL).json()["results"][0]

    assert resultado["amount"] == "80.00"
    assert resultado["pending_amount"] == "50.00"


@pytest.mark.parametrize("rol", ["propietario", "inquilino"])
def test_el_residente_solo_ve_los_cargos_de_su_unidad(
    actores, rol, unit, unit2, make_charge
):
    propio = make_charge(unit, date(2025, 3, 1))
    make_charge(unit2, date(2025, 3, 1))

    data = actores[rol].get(URL).json()

    assert [c["id"] for c in data["results"]] == [str(propio.pk)]


def test_el_residente_no_ve_cargos_anulados(actores, unit, make_charge):
    make_charge(unit, date(2025, 3, 1))
    make_charge(unit, date(2025, 4, 1), status="VOID")

    residente = actores["propietario"].get(URL).json()
    gestion = actores["admin"].get(URL).json()

    assert residente["count"] == 1
    assert gestion["count"] == 2


def test_sin_ocupacion_vigente_no_ve_cargos(tower, unit, make_resident, make_charge):
    from apps.finances.tests.conftest import client_for

    make_charge(unit, date(2025, 3, 1))
    ex_propietario = make_resident(
        "ex@valleblanco.pe", unit, "OWNER", end_date=date(2024, 12, 31)
    )

    data = client_for(ex_propietario).get(URL).json()

    assert data["count"] == 0


def test_filtra_por_unidad(admin_client, unit, unit2, make_charge):
    make_charge(unit, date(2025, 3, 1))
    make_charge(unit2, date(2025, 3, 1))

    data = admin_client.get(URL, {"unit": str(unit2.pk)}).json()

    assert [c["unit_code"] for c in data["results"]] == ["102"]


@pytest.mark.parametrize("valor", ["2025-03", "2025-03-01", "2025-03-15"])
def test_filtra_por_periodo_en_cualquiera_de_sus_formas(
    admin_client, unit, make_charge, valor
):
    make_charge(unit, date(2025, 3, 1))
    make_charge(unit, date(2025, 4, 1))

    data = admin_client.get(URL, {"period": valor}).json()

    assert [c["period"] for c in data["results"]] == ["2025-03-01"]


def test_filtra_por_estado_y_por_concepto(admin_client, unit, make_charge):
    make_charge(unit, date(2025, 3, 1))
    make_charge(unit, date(2025, 4, 1), status="VOID")
    make_charge(unit, date(2025, 5, 1), concept="EXTRAORDINARY_FEE")

    anulados = admin_client.get(URL, {"status": "VOID"}).json()
    extra = admin_client.get(URL, {"concept": "EXTRAORDINARY_FEE"}).json()

    assert [c["period"] for c in anulados["results"]] == ["2025-04-01"]
    assert [c["period"] for c in extra["results"]] == ["2025-05-01"]


@pytest.mark.parametrize(
    "filtro",
    [
        {"status": "INVENTADO"},
        {"period": "marzo"},
        {"period": "2025-13"},
        {"unit": "no-es-uuid"},
        {"concept": "INVENTADO"},
    ],
)
def test_filtros_invalidos_devuelven_400(admin_client, filtro):
    response = admin_client.get(URL, filtro)

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert set(filtro) <= set(response.json()["errors"])


def test_filtrar_por_una_unidad_ajena_devuelve_404(actores, unit2, make_charge):
    make_charge(unit2, date(2025, 3, 1))

    response = actores["propietario"].get(URL, {"unit": str(unit2.pk)})

    assert response.status_code == 404


def test_filtrar_por_una_unidad_inexistente_devuelve_404(actores):
    response = actores["propietario"].get(URL, {"unit": INEXISTENTE})

    assert response.status_code == 404


def test_un_residente_nunca_recibe_cargos_ajenos_aunque_pida_todo(
    actores, unit, unit2, make_charge
):
    make_charge(unit, date(2025, 3, 1))
    ajeno = make_charge(unit2, date(2025, 3, 1))

    ids = {
        c["id"]
        for c in actores["propietario"].get(URL, {"status": "OPEN"}).json()["results"]
    }

    assert str(ajeno.pk) not in ids


# --- Alta manual -------------------------------------------------------------


def cuerpo(unit, **extra):
    data = {
        "unit": str(unit.pk),
        "concept": "EXTRAORDINARY_FEE",
        "period": "2025-03",
        "amount": "150.00",
        "description": "Reparación del portón",
    }
    data.update(extra)
    return data


def test_admin_crea_un_cargo_extraordinario(admin_client, unit):
    response = admin_client.post(URL, cuerpo(unit), format="json")

    assert response.status_code == 201
    data = response.json()
    assert data["amount"] == "150.00"
    assert data["pending_amount"] == "150.00"
    assert data["period"] == "2025-03-01"
    assert data["due_date"] == "2025-03-31"
    assert data["source"] == "MANUAL"
    assert data["status"] == "OPEN"
    assert Charge.objects.get(pk=data["id"]).unit == unit


def test_crear_un_cargo_audita_con_la_ip(admin_client, unit, admin):
    response = admin_client.post(URL, cuerpo(unit), format="json")

    evento = AuditLog.objects.get(action="CHARGE_CREATED")
    assert evento.entity_id == response.json()["id"]
    assert evento.actor_id == str(admin.pk)
    assert evento.ip_address == "127.0.0.1"


def test_acepta_un_vencimiento_propio(admin_client, unit):
    response = admin_client.post(
        URL, cuerpo(unit, due_date="2025-04-15"), format="json"
    )

    assert response.status_code == 201
    assert response.json()["due_date"] == "2025-04-15"


def test_rechaza_un_vencimiento_anterior_al_periodo(admin_client, unit):
    response = admin_client.post(
        URL, cuerpo(unit, due_date="2025-02-28"), format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_charge"


def test_crea_una_cuota_ordinaria_que_faltaba(admin_client, unit):
    response = admin_client.post(
        URL, cuerpo(unit, concept="ORDINARY_FEE", amount="80.00"), format="json"
    )

    assert response.status_code == 201


def test_una_cuota_ordinaria_repetida_devuelve_409(admin_client, unit, make_charge):
    make_charge(unit, date(2025, 3, 1))

    response = admin_client.post(
        URL, cuerpo(unit, concept="ORDINARY_FEE", amount="80.00"), format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "charge_exists"
    assert Charge.objects.count() == 1


@pytest.mark.parametrize("monto", ["0", "-10.00", "10.123", "abc", ""])
def test_rechaza_montos_invalidos(admin_client, unit, monto):
    response = admin_client.post(URL, cuerpo(unit, amount=monto), format="json")

    assert response.status_code == 400
    assert not Charge.objects.exists()


@pytest.mark.parametrize("concepto", ["LATE_FEE", "AGREEMENT_INSTALLMENT", "X"])
def test_rechaza_conceptos_no_manuales(admin_client, unit, concepto):
    response = admin_client.post(URL, cuerpo(unit, concept=concepto), format="json")

    assert response.status_code == 400
    assert "concept" in response.json()["errors"]


def test_rechaza_una_unidad_inexistente(admin_client):
    response = admin_client.post(
        URL, {**cuerpo_base(), "unit": INEXISTENTE}, format="json"
    )

    assert response.status_code == 400
    assert "unit" in response.json()["errors"]


def cuerpo_base():
    return {
        "concept": "EXTRAORDINARY_FEE",
        "period": "2025-03",
        "amount": "150.00",
    }


def test_rechaza_una_unidad_inactiva(admin_client, unit):
    unit.is_active = False
    unit.save()

    response = admin_client.post(URL, cuerpo(unit), format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_charge"


def test_faltan_campos_obligatorios(admin_client):
    response = admin_client.post(URL, {}, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert {"unit", "concept", "period", "amount"} <= set(response.json()["errors"])


def test_periodo_invalido_devuelve_400(admin_client, unit):
    response = admin_client.post(URL, cuerpo(unit, period="marzo"), format="json")

    assert response.status_code == 400
    assert "period" in response.json()["errors"]
