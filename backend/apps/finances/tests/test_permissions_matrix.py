"""S3-B: matriz rol x endpoint. Una prueba por celda."""

from datetime import date

import pytest

pytestmark = pytest.mark.django_db

ROLES = (
    "anonimo",
    "admin",
    "admin_rol",
    "junta",
    "tesorero",
    "propietario",
    "inquilino",
    "vecino",
)
TODOS = set(ROLES) - {"anonimo"}
ADMIN = {"admin", "admin_rol"}
GESTION = ADMIN | {"junta", "tesorero"}

TARIFA = {
    "concept": "ORDINARY_FEE",
    "amount": "90.00",
    "valid_from": "2025-06-01",
}
CARGO = {"concept": "EXTRAORDINARY_FEE", "period": "2025-03", "amount": "150.00"}

# nombre, método, ruta(ctx), cuerpo(ctx), éxito, quiénes pasan, código al resto
ENDPOINTS = (
    ("GET fee-rates", "get", lambda c: "/api/v1/fee-rates/", None, 200, TODOS, 403),
    (
        "POST fee-rates",
        "post",
        lambda c: "/api/v1/fee-rates/",
        lambda c: {**TARIFA, "tower": str(c["tower"].pk)},
        201,
        ADMIN,
        403,
    ),
    ("GET charges", "get", lambda c: "/api/v1/charges/", None, 200, TODOS, 403),
    (
        "POST charges",
        "post",
        lambda c: "/api/v1/charges/",
        lambda c: {**CARGO, "unit": str(c["unit"].pk)},
        201,
        ADMIN,
        403,
    ),
    (
        "POST charges/generate",
        "post",
        lambda c: "/api/v1/charges/generate/",
        lambda c: {"period": "2025-03"},
        200,
        ADMIN,
        403,
    ),
    (
        "POST charges/{id}/void",
        "post",
        lambda c: f"/api/v1/charges/{c['charge'].pk}/void/",
        lambda c: {"reason": "error de digitación"},
        200,
        ADMIN,
        403,
    ),
    (
        "GET units/{id}/statement",
        "get",
        lambda c: f"/api/v1/units/{c['unit'].pk}/statement/",
        None,
        200,
        TODOS - {"vecino"},
        404,
    ),
    (
        "GET reports/delinquency",
        "get",
        lambda c: "/api/v1/reports/delinquency/",
        None,
        200,
        GESTION,
        403,
    ),
)


@pytest.fixture
def ctx(tower, unit, fee_rate, make_charge):
    return {
        "tower": tower,
        "unit": unit,
        "charge": make_charge(unit, date(2025, 2, 1)),
    }


def enviar(client, metodo, ruta, cuerpo):
    if metodo == "get":
        return client.get(ruta)
    return client.post(ruta, cuerpo, format="json")


@pytest.mark.parametrize("rol", ROLES)
@pytest.mark.parametrize(
    "endpoint", ENDPOINTS, ids=[endpoint[0] for endpoint in ENDPOINTS]
)
def test_matriz_rol_por_endpoint(actores, ctx, endpoint, rol):
    _, metodo, ruta, cuerpo, exito, permitidos, prohibido = endpoint

    response = enviar(
        actores[rol],
        metodo,
        ruta(ctx),
        cuerpo(ctx) if cuerpo else None,
    )

    if rol == "anonimo":
        assert response.status_code in (401, 403)
    elif rol in permitidos:
        assert response.status_code == exito
    else:
        assert response.status_code == prohibido


def test_un_residente_no_puede_anular_ni_su_propio_cargo(actores, ctx):
    response = actores["propietario"].post(
        f"/api/v1/charges/{ctx['charge'].pk}/void/", {"reason": "x"}, format="json"
    )

    assert response.status_code == 403
    ctx["charge"].refresh_from_db()
    assert ctx["charge"].status == "OPEN"


def test_un_vecino_no_ve_cargos_ni_por_el_listado_ni_por_el_filtro(actores, ctx):
    listado = actores["vecino"].get("/api/v1/charges/").json()
    filtrado = actores["vecino"].get("/api/v1/charges/", {"unit": str(ctx["unit"].pk)})

    assert listado["count"] == 0
    assert filtrado.status_code == 404
