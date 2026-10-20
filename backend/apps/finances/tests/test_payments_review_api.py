"""S4-B: aprobar, rechazar y anular pagos por la API, y los flujos completos."""

from datetime import date

import pytest

from apps.core.models import AuditLog
from apps.finances import models

pytestmark = pytest.mark.django_db

REVISORES = ("admin", "admin_rol", "tesorero")
NO_REVISORES = ("junta", "propietario", "inquilino", "vecino")


def accion(pago, nombre):
    return f"/api/v1/payments/{pago.pk}/{nombre}/"


@pytest.fixture
def pendiente(unit, make_payment, usuario):
    return make_payment(unit, "80.00", submitted_by=usuario("propietario"))


@pytest.fixture
def cargos(unit, make_charge):
    return [make_charge(unit, date(2025, m, 1)) for m in (1, 2, 3)]


def estado_de_cuenta(client, unit):
    return client.get(
        f"/api/v1/units/{unit.pk}/statement/", {"as_of": "2025-03-10"}
    ).json()


# --- Aprobar ------------------------------------------------------------------


@pytest.mark.parametrize("rol", REVISORES)
def test_administracion_y_tesoreria_aprueban(actores, pendiente, cargos, rol):
    response = actores[rol].post(accion(pendiente, "approve"))

    assert response.status_code == 200
    datos = response.json()
    assert datos["status"] == "APPROVED"
    assert datos["allocated_amount"] == "80.00"
    pendiente.refresh_from_db()
    assert pendiente.status == "APPROVED"
    assert pendiente.reviewed_by is not None


@pytest.mark.parametrize("rol", NO_REVISORES)
def test_los_demas_roles_no_aprueban(actores, pendiente, cargos, rol):
    response = actores[rol].post(accion(pendiente, "approve"))

    assert response.status_code == 403
    pendiente.refresh_from_db()
    assert pendiente.status == "PENDING"


def test_el_anonimo_no_aprueba(actores, pendiente):
    response = actores["anonimo"].post(accion(pendiente, "approve"))

    assert response.status_code in (401, 403)


def test_aprobar_aplica_al_cargo_mas_antiguo_primero(actores, unit, pendiente, cargos):
    actores["tesorero"].post(accion(pendiente, "approve"))

    asignaciones = models.PaymentAllocation.objects.get()
    assert asignaciones.charge == cargos[0]


@pytest.mark.parametrize("estado", ["APPROVED", "REJECTED", "VOID"])
def test_aprobar_un_pago_que_no_esta_pendiente_da_409(
    actores, unit, make_payment, estado
):
    pago = make_payment(unit, status=estado)

    response = actores["admin"].post(accion(pago, "approve"))

    assert response.status_code == 409
    assert response.json()["code"] == "payment_not_pending"


def test_aprobar_un_pago_inexistente_da_404(actores, pendiente):
    import uuid

    response = actores["admin"].post(f"/api/v1/payments/{uuid.uuid4()}/approve/")

    assert response.status_code == 404


def test_quien_registro_el_pago_tambien_puede_aprobarlo(
    actores, unit, make_payment, usuario
):
    pago = make_payment(unit, source="STAFF", submitted_by=usuario("tesorero"))

    response = actores["tesorero"].post(accion(pago, "approve"))

    assert response.status_code == 200


# --- Rechazar -----------------------------------------------------------------


@pytest.mark.parametrize("rol", REVISORES)
def test_administracion_y_tesoreria_rechazan_con_motivo(actores, pendiente, rol):
    response = actores[rol].post(
        accion(pendiente, "reject"), {"reason": "Monto distinto"}, format="json"
    )

    assert response.status_code == 200
    datos = response.json()
    assert datos["status"] == "REJECTED"
    assert datos["rejection_reason"] == "Monto distinto"


@pytest.mark.parametrize("rol", NO_REVISORES)
def test_los_demas_roles_no_rechazan(actores, pendiente, rol):
    response = actores[rol].post(
        accion(pendiente, "reject"), {"reason": "x"}, format="json"
    )

    assert response.status_code == 403
    pendiente.refresh_from_db()
    assert pendiente.status == "PENDING"


@pytest.mark.parametrize("cuerpo", [{}, {"reason": ""}, {"reason": "   "}])
def test_rechazar_exige_motivo(actores, pendiente, cuerpo):
    response = actores["admin"].post(accion(pendiente, "reject"), cuerpo, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "reason" in response.json()["errors"]


def test_el_motivo_no_puede_pasar_de_255_caracteres(actores, pendiente):
    response = actores["admin"].post(
        accion(pendiente, "reject"), {"reason": "x" * 256}, format="json"
    )

    assert response.status_code == 400


def test_rechazar_un_pago_que_no_esta_pendiente_da_409(actores, unit, pay):
    pago = pay(unit)

    response = actores["admin"].post(
        accion(pago, "reject"), {"reason": "tarde"}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "payment_not_pending"


def test_el_residente_ve_el_motivo_del_rechazo(actores, pendiente):
    actores["tesorero"].post(
        accion(pendiente, "reject"), {"reason": "Foto ilegible"}, format="json"
    )

    datos = actores["propietario"].get(f"/api/v1/payments/{pendiente.pk}/").json()

    assert datos["status"] == "REJECTED"
    assert datos["rejection_reason"] == "Foto ilegible"


# --- Anular -------------------------------------------------------------------


@pytest.mark.parametrize("rol", ["admin", "admin_rol"])
def test_solo_la_administracion_anula(actores, unit, pay, rol):
    pago = pay(unit)

    response = actores[rol].post(
        accion(pago, "void"), {"reason": "Error"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "VOID"


@pytest.mark.parametrize(
    "rol", ["tesorero", "junta", "propietario", "inquilino", "vecino"]
)
def test_tesoreria_y_los_demas_no_anulan(actores, unit, pay, rol):
    pago = pay(unit)

    response = actores[rol].post(accion(pago, "void"), {"reason": "x"}, format="json")

    assert response.status_code == 403
    pago.refresh_from_db()
    assert pago.status == "APPROVED"


@pytest.mark.parametrize("cuerpo", [{}, {"reason": " "}])
def test_anular_exige_motivo(actores, unit, pay, cuerpo):
    pago = pay(unit)

    response = actores["admin"].post(accion(pago, "void"), cuerpo, format="json")

    assert response.status_code == 400
    assert "reason" in response.json()["errors"]


@pytest.mark.parametrize("estado", ["PENDING", "REJECTED", "VOID"])
def test_anular_un_pago_que_no_esta_aprobado_da_409(
    actores, unit, make_payment, estado
):
    pago = make_payment(unit, status=estado)

    response = actores["admin"].post(
        accion(pago, "void"), {"reason": "x"}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "payment_not_approved"


# --- Flujos completos ---------------------------------------------------------


def test_flujo_registrar_subir_comprobante_aprobar_y_ver_el_estado_de_cuenta(
    actores, unit, cargos, datos_pago, subir, voucher_file
):
    residente, tesorero = actores["propietario"], actores["tesorero"]
    antes = estado_de_cuenta(residente, unit)["summary"]
    assert antes["pending_balance"] == "240.00"

    creado = residente.post(
        "/api/v1/payments/", datos_pago(unit, amount="160.00"), format="json"
    )
    assert creado.status_code == 201
    pago = models.Payment.objects.get()
    assert subir(residente, pago, voucher_file()).status_code == 201
    assert tesorero.post(accion(pago, "approve")).status_code == 200

    despues = estado_de_cuenta(residente, unit)
    assert despues["summary"]["pending_balance"] == "80.00"
    assert despues["summary"]["credit_balance"] == "0.00"
    assert [c["allocated"] for c in despues["charges"]] == ["80.00", "80.00", "0.00"]


def test_flujo_con_excedente_deja_saldo_a_favor_en_el_estado_de_cuenta(
    actores, unit, make_charge, datos_pago
):
    make_charge(unit, date(2025, 3, 1))
    residente = actores["propietario"]
    residente.post(
        "/api/v1/payments/", datos_pago(unit, amount="100.00"), format="json"
    )
    pago = models.Payment.objects.get()

    detalle = actores["admin"].post(accion(pago, "approve")).json()

    assert detalle["credit_remainder"] == "20.00"
    resumen = estado_de_cuenta(residente, unit)["summary"]
    assert resumen["pending_balance"] == "0.00"
    assert resumen["credit_balance"] == "20.00"


def test_flujo_rechazado_no_cambia_el_estado_de_cuenta(
    actores, unit, cargos, datos_pago
):
    residente = actores["propietario"]
    residente.post("/api/v1/payments/", datos_pago(unit), format="json")
    pago = models.Payment.objects.get()

    actores["admin"].post(
        accion(pago, "reject"), {"reason": "No coincide"}, format="json"
    )

    resumen = estado_de_cuenta(residente, unit)["summary"]
    assert resumen["pending_balance"] == "240.00"
    assert resumen["credit_balance"] == "0.00"


def test_flujo_anular_recalcula_deuda_y_saldo(actores, unit, cargos, pay):
    residente = actores["propietario"]
    pago = pay(unit, "100.00")
    assert estado_de_cuenta(residente, unit)["summary"]["pending_balance"] == "140.00"

    response = actores["admin"].post(
        accion(pago, "void"), {"reason": "Depósito devuelto"}, format="json"
    )

    assert response.status_code == 200
    resumen = estado_de_cuenta(residente, unit)["summary"]
    assert resumen["pending_balance"] == "240.00"
    assert resumen["credit_balance"] == "0.00"


def test_el_estado_de_cuenta_expone_el_saldo_a_favor_como_texto(
    actores, unit, make_charge, pay
):
    make_charge(unit, date(2025, 3, 1))
    pay(unit, "130.50")

    resumen = estado_de_cuenta(actores["inquilino"], unit)["summary"]

    assert resumen["credit_balance"] == "50.50"


def test_el_resumen_sin_pagos_trae_saldo_a_favor_cero(actores, unit, cargos):
    resumen = estado_de_cuenta(actores["admin"], unit)["summary"]

    assert resumen["credit_balance"] == "0.00"


# --- Auditoría con IP ---------------------------------------------------------

IP = {"REMOTE_ADDR": "203.0.113.7"}


def test_registrar_un_pago_deja_rastro_sin_el_numero_completo(
    actores, unit, datos_pago, usuario
):
    actores["propietario"].post(
        "/api/v1/payments/",
        datos_pago(unit, operation_number="AB-98765432"),
        format="json",
        **IP,
    )

    evento = AuditLog.objects.get(action="PAYMENT_SUBMITTED")
    assert evento.ip_address == "203.0.113.7"
    assert evento.actor_id == str(usuario("propietario").pk)
    assert evento.changes["operation_last4"] == "5432"
    assert "AB98765432" not in str(evento.changes)
    assert "98765432" not in str(evento.changes)


def test_subir_un_comprobante_deja_rastro_sin_el_contenido(
    actores, pendiente, subir, voucher_file
):
    contenido = voucher_file("jpg", salt=b"SECRETO-DEL-ARCHIVO")
    from django.core.files.uploadedfile import SimpleUploadedFile

    actores["propietario"].post(
        f"/api/v1/payments/{pendiente.pk}/voucher/",
        {"file": SimpleUploadedFile("c.jpg", contenido, content_type="image/jpeg")},
        format="multipart",
        **IP,
    )

    evento = AuditLog.objects.get(action="VOUCHER_ATTACHED")
    assert evento.ip_address == "203.0.113.7"
    assert "SECRETO" not in str(evento.changes)
    assert "file" not in evento.changes


def test_aprobar_deja_rastro_con_la_ip(actores, pendiente, cargos):
    actores["tesorero"].post(accion(pendiente, "approve"), **IP)

    evento = AuditLog.objects.get(action="PAYMENT_APPROVED")
    assert evento.ip_address == "203.0.113.7"
    assert evento.actor_label == "tesorero@valleblanco.pe"


def test_rechazar_deja_rastro_con_la_ip(actores, pendiente):
    actores["tesorero"].post(
        accion(pendiente, "reject"), {"reason": "Ilegible"}, format="json", **IP
    )

    evento = AuditLog.objects.get(action="PAYMENT_REJECTED")
    assert evento.ip_address == "203.0.113.7"
    assert evento.changes["reason"] == "Ilegible"


def test_anular_deja_rastro_con_la_ip(actores, unit, pay):
    pago = pay(unit)

    actores["admin"].post(
        accion(pago, "void"), {"reason": "Error"}, format="json", **IP
    )

    evento = AuditLog.objects.get(action="PAYMENT_VOIDED")
    assert evento.ip_address == "203.0.113.7"


def test_una_accion_rechazada_por_permisos_no_deja_rastro(actores, pendiente):
    actores["propietario"].post(accion(pendiente, "approve"))

    assert not AuditLog.objects.filter(action="PAYMENT_APPROVED").exists()
