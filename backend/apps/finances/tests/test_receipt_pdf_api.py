"""S5-A: `GET /payments/{id}/receipt/pdf/` y `receipt_number` en el pago."""

from datetime import date

import pytest

from apps.core.models import AuditLog
from apps.finances import services

from .conftest import client_for
from .utilidades import MARCA_ESTILOS, sin_etiquetas, texto

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("pdf_falso")]

OPERACION = "BCP98765432"
ESPERADO = {
    "anonimo": 403,
    "admin": 200,
    "admin_rol": 200,
    "junta": 403,
    "tesorero": 200,
    "propietario": 200,
    "inquilino": 200,
    "vecino": 404,
}


def url(pago):
    return f"/api/v1/payments/{pago.pk}/receipt/pdf/"


def detalle(pago):
    return f"/api/v1/payments/{pago.pk}/"


@pytest.fixture
def pago_aprobado(actores, unit, make_charge, make_payment, admin):
    """Pago aprobado de la unidad 101 (cubre enero 2025) con su recibo emitido.

    El titular principal de la 101 es `propietario` (de `actores`); se le pone un
    nombre reconocible para revisar el contenido.
    """
    from apps.properties.models import Person

    Person.objects.filter(full_name="propietario").update(full_name="Rosa Quispe")
    make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, "80.00", operation_number=OPERACION)
    services.approve_payment(pago, actor=admin)
    return pago


# --- Matriz rol x endpoint ---------------------------------------------------


@pytest.mark.parametrize("rol", list(ESPERADO))
def test_matriz_de_roles_del_recibo_pdf(actores, pago_aprobado, rol):
    respuesta = actores[rol].get(url(pago_aprobado))

    assert respuesta.status_code == ESPERADO[rol]


def test_la_junta_que_tambien_reside_en_la_unidad_descarga_el_recibo(
    unit, pago_aprobado, make_resident, give_role
):
    usuario = make_resident("junta-residente@valleblanco.pe", unit, "OWNER")
    give_role(usuario, "BOARD")

    assert client_for(usuario).get(url(pago_aprobado)).status_code == 200


def test_un_pago_ajeno_responde_igual_que_uno_inexistente(actores, pago_aprobado):
    """No se revela si el pago existe (IDOR entre unidades)."""
    import uuid

    ajeno = actores["vecino"].get(url(pago_aprobado))
    inexistente = actores["vecino"].get(f"/api/v1/payments/{uuid.uuid4()}/receipt/pdf/")

    assert ajeno.status_code == inexistente.status_code == 404
    assert ajeno.content == inexistente.content


def test_el_vecino_no_baja_el_recibo_ni_aunque_conozca_el_id(
    actores, unit2, make_payment, admin
):
    propio = make_payment(unit2, "80.00")
    services.approve_payment(propio, actor=admin)

    assert actores["vecino"].get(url(propio)).status_code == 200
    assert actores["propietario"].get(url(propio)).status_code == 404
    assert actores["inquilino"].get(url(propio)).status_code == 404


# --- Respuesta ---------------------------------------------------------------


def test_cabeceras_de_la_descarga(actores, pago_aprobado, recibo_de):
    numero = recibo_de(pago_aprobado).number

    respuesta = actores["propietario"].get(url(pago_aprobado))

    assert respuesta["Content-Type"] == "application/pdf"
    assert respuesta["X-Content-Type-Options"] == "nosniff"
    assert respuesta["Cache-Control"] == "private"
    assert (
        respuesta["Content-Disposition"]
        == f'attachment; filename="recibo-{numero}.pdf"'
    )
    assert respuesta.content.startswith(b"%PDF")


def test_el_nombre_del_archivo_es_generico(actores, pago_aprobado):
    disposicion = actores["admin"].get(url(pago_aprobado))["Content-Disposition"]

    assert "101" not in disposicion
    assert "Rosa" not in disposicion
    assert OPERACION not in disposicion


@pytest.mark.parametrize("metodo", ["post", "put", "patch", "delete"])
def test_solo_se_permite_get(actores, pago_aprobado, metodo):
    respuesta = getattr(actores["admin"], metodo)(url(pago_aprobado))

    assert respuesta.status_code == 405


# --- Estados del pago --------------------------------------------------------


@pytest.mark.parametrize("rol", ["admin", "tesorero", "propietario"])
def test_un_pago_pendiente_responde_409(actores, unit, make_payment, rol):
    pago = make_payment(unit)

    respuesta = actores[rol].get(url(pago))

    assert respuesta.status_code == 409
    cuerpo = respuesta.json()
    assert cuerpo["code"] == "payment_not_approved"
    assert set(cuerpo) == {"code", "detail", "errors"}


def test_un_pago_rechazado_responde_409(actores, unit, make_payment, admin):
    pago = make_payment(unit)
    services.reject_payment(pago, "Foto ilegible", actor=admin)

    respuesta = actores["propietario"].get(url(pago))

    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "payment_not_approved"


def test_la_junta_sin_tesoreria_recibe_403_antes_que_el_estado(
    actores, unit, make_payment
):
    pago = make_payment(unit)

    assert actores["junta"].get(url(pago)).status_code == 403


@pytest.mark.parametrize("origen", ["legacy", "directo"])
def test_un_pago_aprobado_sin_recibo_responde_404(actores, unit, make_payment, origen):
    if origen == "legacy":
        pago = make_payment(
            unit, method="LEGACY", operation_number="", status="APPROVED"
        )
    else:
        pago = make_payment(unit, status="APPROVED")

    respuesta = actores["admin"].get(url(pago))

    assert respuesta.status_code == 404
    assert respuesta.json()["code"] == "receipt_not_found"


def test_un_pago_inexistente_responde_404(actores):
    import uuid

    respuesta = actores["admin"].get(f"/api/v1/payments/{uuid.uuid4()}/receipt/pdf/")

    assert respuesta.status_code == 404


def test_un_pago_anulado_conserva_el_recibo_marcado_anulado(
    actores, pago_aprobado, admin
):
    antes = texto(actores["propietario"].get(url(pago_aprobado)))
    services.void_payment(pago_aprobado, "Pago duplicado", actor=admin)

    respuesta = actores["propietario"].get(url(pago_aprobado))

    assert "ANULADO" not in antes
    assert respuesta.status_code == 200
    assert "ANULADO" in texto(respuesta)


def test_el_motivo_de_la_anulacion_no_se_imprime_en_el_recibo(
    actores, pago_aprobado, admin
):
    services.void_payment(pago_aprobado, "Motivo interno reservado", actor=admin)

    assert "Motivo interno reservado" not in texto(
        actores["propietario"].get(url(pago_aprobado))
    )


# --- Contenido ---------------------------------------------------------------


def test_el_recibo_trae_los_datos_del_snapshot(actores, pago_aprobado, recibo_de):
    numero = recibo_de(pago_aprobado).number

    visible = sin_etiquetas(texto(actores["propietario"].get(url(pago_aprobado))))

    assert numero in visible
    assert "101" in visible
    assert "Torre 2" in visible
    assert "Rosa Quispe" in visible
    assert "S/ 80.00" in visible
    assert "Transferencia" in visible
    assert "01/03/2025" in visible
    assert "Enero 2025" in visible
    assert "****5432" in visible


def test_el_recibo_aclara_que_no_es_comprobante_tributario(actores, pago_aprobado):
    visible = sin_etiquetas(texto(actores["admin"].get(url(pago_aprobado))))

    assert "Recibo interno" in visible
    assert "sin valor tributario" in visible


def test_el_recibo_nunca_imprime_el_numero_de_operacion_completo(
    actores, pago_aprobado
):
    for rol in ("admin", "tesorero", "propietario", "inquilino"):
        assert OPERACION not in texto(actores[rol].get(url(pago_aprobado)))


def test_el_pdf_sale_del_snapshot_no_de_los_datos_actuales(
    actores, pago_aprobado, unit
):
    from apps.properties.models import Person

    Person.objects.filter(full_name="Rosa Quispe").update(full_name="Persona Nueva")

    visible = sin_etiquetas(texto(actores["admin"].get(url(pago_aprobado))))

    assert "Rosa Quispe" in visible
    assert "Persona Nueva" not in visible


def test_sin_titular_el_recibo_lo_indica(
    actores, tower, make_unit, make_payment, admin
):
    pago = make_payment(make_unit(tower, "103"))
    services.approve_payment(pago, actor=admin)

    visible = sin_etiquetas(texto(actores["admin"].get(url(pago))))

    assert "Sin titular principal registrado" in visible


def test_el_saldo_a_favor_generado_se_muestra(actores, unit, make_payment, admin):
    pago = make_payment(unit, "100.00")
    services.approve_payment(pago, actor=admin)

    visible = sin_etiquetas(texto(actores["admin"].get(url(pago))))

    assert "Saldo a favor generado" in visible
    assert "S/ 100.00" in visible


def test_el_html_escapa_el_texto_de_los_cargos(
    actores, unit, make_charge, make_payment, admin
):
    make_charge(unit, date(2025, 1, 1), description="<script>alert(1)</script>")
    pago = make_payment(unit)
    services.approve_payment(pago, actor=admin)

    html = texto(actores["admin"].get(url(pago)))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_el_recibo_usa_la_hoja_de_estilos_comun(actores, pago_aprobado):
    assert MARCA_ESTILOS in texto(actores["admin"].get(url(pago_aprobado)))


# --- receipt_number en el pago ----------------------------------------------


def test_el_pago_aprobado_informa_su_numero_de_recibo(
    actores, pago_aprobado, recibo_de
):
    numero = recibo_de(pago_aprobado).number

    for rol in ("admin", "junta", "tesorero", "propietario", "inquilino"):
        assert (
            actores[rol].get(detalle(pago_aprobado)).json()["receipt_number"] == numero
        )


@pytest.mark.parametrize("estado", ["PENDING", "REJECTED"])
def test_un_pago_sin_recibo_informa_receipt_number_nulo(
    actores, unit, make_payment, estado
):
    pago = make_payment(unit, status=estado)

    assert actores["admin"].get(detalle(pago)).json()["receipt_number"] is None


def test_un_pago_legacy_informa_receipt_number_nulo(actores, unit, make_payment):
    pago = make_payment(unit, method="LEGACY", operation_number="", status="APPROVED")

    assert actores["admin"].get(detalle(pago)).json()["receipt_number"] is None


def test_el_listado_trae_receipt_number_sin_consultas_por_pago(
    actores, unit, make_payment, admin, django_assert_max_num_queries
):
    for _ in range(5):
        services.approve_payment(make_payment(unit), actor=admin)

    with django_assert_max_num_queries(12):
        respuesta = actores["admin"].get("/api/v1/payments/")

    numeros = [p["receipt_number"] for p in respuesta.json()["results"]]
    assert len(numeros) == 5
    assert all(numeros)


# --- Flujo completo y auditoría ---------------------------------------------


def test_flujo_registrar_subir_aprobar_y_descargar_el_recibo(
    actores, unit, make_charge, datos_pago, subir, voucher_file, recibo_de
):
    make_charge(unit, date(2025, 1, 1))
    propietario, tesorero = actores["propietario"], actores["tesorero"]

    creado = propietario.post("/api/v1/payments/", datos_pago(unit), format="json")
    assert creado.status_code == 201
    pago_id = creado.json()["id"]
    assert creado.json()["receipt_number"] is None
    assert (
        propietario.get(f"/api/v1/payments/{pago_id}/receipt/pdf/").status_code == 409
    )

    from apps.finances.models import Payment

    pago = Payment.objects.get(pk=pago_id)
    assert subir(propietario, pago, voucher_file("jpg")).status_code == 201
    aprobado = tesorero.post(f"/api/v1/payments/{pago_id}/approve/")
    assert aprobado.status_code == 200
    numero = recibo_de(pago).number
    assert aprobado.json()["receipt_number"] == numero

    descarga = propietario.get(f"/api/v1/payments/{pago_id}/receipt/pdf/")
    assert descarga.status_code == 200
    assert numero in texto(descarga)
    assert propietario.get(detalle(pago)).json()["receipt_number"] == numero


def test_aprobar_por_la_api_audita_la_emision_con_la_ip(actores, unit, make_payment):
    pago = make_payment(unit)

    actores["tesorero"].post(
        f"/api/v1/payments/{pago.pk}/approve/", REMOTE_ADDR="203.0.113.9"
    )

    evento = AuditLog.objects.get(action="RECEIPT_ISSUED")
    assert evento.ip_address == "203.0.113.9"


def test_descargar_el_recibo_no_se_audita(actores, pago_aprobado):
    antes = AuditLog.objects.count()

    actores["propietario"].get(url(pago_aprobado))

    assert AuditLog.objects.count() == antes


def test_si_no_hay_renderizador_responde_503(actores, pago_aprobado, monkeypatch):
    from apps.finances import pdf

    def sin_pdf(html):
        raise pdf.PdfNoDisponibleError("sin WeasyPrint")

    monkeypatch.setattr(pdf, "render_pdf", sin_pdf)

    respuesta = actores["admin"].get(url(pago_aprobado))

    assert respuesta.status_code == 503
    assert respuesta.json()["code"] == "pdf_unavailable"
