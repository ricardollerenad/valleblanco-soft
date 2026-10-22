"""S4-B: matriz rol x endpoint de pagos. Una prueba por celda."""

from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.finances import services

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
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"matriz"

# nombre, método, ruta(ctx), cuerpo(ctx), formato, éxito, quiénes pasan,
# código al resto, códigos distintos por rol
ENDPOINTS = (
    (
        "GET payments",
        "get",
        lambda c: "/api/v1/payments/",
        None,
        "json",
        200,
        TODOS,
        403,
        {},
    ),
    (
        "GET payments/{id}",
        "get",
        lambda c: f"/api/v1/payments/{c['pendiente'].pk}/",
        None,
        "json",
        200,
        TODOS - {"vecino"},
        404,
        {},
    ),
    (
        "POST payments",
        "post",
        lambda c: "/api/v1/payments/",
        lambda c: {
            "unit": str(c["unit"].pk),
            "amount": "80.00",
            "method": "PLIN",
            "operation_number": "MATRIZ1",
            "paid_at": "2025-03-01",
        },
        "json",
        201,
        ADMIN | {"tesorero", "propietario", "inquilino"},
        404,
        {},
    ),
    (
        "POST payments/{id}/voucher",
        "post",
        lambda c: f"/api/v1/payments/{c['pendiente'].pk}/voucher/",
        lambda c: {"file": SimpleUploadedFile("c.jpg", JPG, content_type="image/jpeg")},
        "multipart",
        201,
        ADMIN | {"tesorero", "propietario"},
        403,
        {"vecino": 404},
    ),
    (
        "GET payments/{id}/voucher",
        "get",
        lambda c: f"/api/v1/payments/{c['aprobado'].pk}/voucher/",
        None,
        "json",
        200,
        ADMIN | {"tesorero", "propietario", "inquilino"},
        403,
        {"vecino": 404},
    ),
    (
        "POST payments/{id}/approve",
        "post",
        lambda c: f"/api/v1/payments/{c['pendiente'].pk}/approve/",
        None,
        "json",
        200,
        ADMIN | {"tesorero"},
        403,
        {},
    ),
    (
        "POST payments/{id}/reject",
        "post",
        lambda c: f"/api/v1/payments/{c['pendiente'].pk}/reject/",
        lambda c: {"reason": "No coincide"},
        "json",
        200,
        ADMIN | {"tesorero"},
        403,
        {},
    ),
    (
        "POST payments/{id}/void",
        "post",
        lambda c: f"/api/v1/payments/{c['aprobado'].pk}/void/",
        lambda c: {"reason": "Error"},
        "json",
        200,
        ADMIN,
        403,
        {},
    ),
)


@pytest.fixture
def ctx(unit, fee_rate, make_charge, make_payment, usuario, voucher_file, actores):
    make_charge(unit, date(2025, 2, 1))
    pendiente = make_payment(unit, submitted_by=usuario("propietario"))
    aprobado = make_payment(unit, "50.00", submitted_by=usuario("propietario"))
    services.attach_voucher(aprobado, voucher_file())
    services.approve_payment(aprobado)
    return {"unit": unit, "pendiente": pendiente, "aprobado": aprobado}


def enviar(client, metodo, ruta, cuerpo, formato):
    if metodo == "get":
        return client.get(ruta)
    return client.post(ruta, cuerpo, format=formato)


@pytest.mark.parametrize("rol", ROLES)
@pytest.mark.parametrize(
    "endpoint", ENDPOINTS, ids=[endpoint[0] for endpoint in ENDPOINTS]
)
def test_matriz_rol_por_endpoint(actores, ctx, endpoint, rol):
    _, metodo, ruta, cuerpo, formato, exito, permitidos, prohibido, especiales = (
        endpoint
    )

    response = enviar(
        actores[rol], metodo, ruta(ctx), cuerpo(ctx) if cuerpo else None, formato
    )

    if rol == "anonimo":
        assert response.status_code in (401, 403)
    elif rol in permitidos:
        assert response.status_code == exito
    else:
        assert response.status_code == especiales.get(rol, prohibido)


def test_el_vecino_no_puede_tocar_un_pago_ajeno_por_ninguna_ruta(actores, ctx):
    pago = ctx["aprobado"]
    rutas = (
        ("get", f"/api/v1/payments/{pago.pk}/"),
        ("get", f"/api/v1/payments/{pago.pk}/voucher/"),
        ("post", f"/api/v1/payments/{pago.pk}/voucher/"),
    )

    codigos = [
        getattr(actores["vecino"], metodo)(ruta).status_code for metodo, ruta in rutas
    ]

    assert codigos == [404, 404, 404]
