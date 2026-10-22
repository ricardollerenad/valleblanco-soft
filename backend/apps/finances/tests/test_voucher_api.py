"""S4-B: POST y GET /payments/{id}/voucher/ (subida y descarga privadas)."""

import uuid

import pytest

from apps.finances import models, services
from apps.finances.models import MAX_VOUCHER_SIZE

pytestmark = pytest.mark.django_db


def url(pago):
    return f"/api/v1/payments/{pago.pk}/voucher/"


@pytest.fixture
def pago(unit, make_payment, usuario):
    """Pago pendiente de la 101 registrado por el propietario."""
    return make_payment(unit, submitted_by=usuario("propietario"))


# --- Subida -------------------------------------------------------------------


def test_quien_registro_el_pago_sube_su_comprobante(
    actores, pago, subir, voucher_file, voucher_root
):
    response = subir(actores["propietario"], pago, voucher_file("jpg"))

    assert response.status_code == 201
    voucher = pago.vouchers.get()
    assert voucher.content_type == "image/jpeg"
    assert len(list(voucher_root.rglob("*.jpg"))) == 1


def test_la_respuesta_trae_los_datos_del_comprobante_y_las_advertencias(
    actores, pago, subir, voucher_file
):
    contenido = voucher_file("jpg")

    datos = subir(actores["propietario"], pago, contenido).json()

    assert set(datos) == {"id", "content_type", "size", "created_at", "warnings"}
    assert datos["id"] == str(pago.vouchers.get().pk)
    assert datos["size"] == len(contenido)
    assert datos["warnings"] == []


@pytest.mark.parametrize(
    ("tipo", "mime"),
    [
        ("png", "image/png"),
        ("webp", "image/webp"),
        ("pdf", "application/pdf"),
    ],
)
def test_acepta_png_webp_y_pdf(actores, pago, subir, voucher_file, tipo, mime):
    response = subir(actores["propietario"], pago, voucher_file(tipo))

    assert response.status_code == 201
    assert response.json()["content_type"] == mime


@pytest.mark.parametrize("rol", ["admin", "admin_rol", "tesorero"])
def test_administracion_y_tesoreria_suben_comprobantes_de_cualquier_pago(
    actores, pago, subir, voucher_file, rol
):
    assert subir(actores[rol], pago, voucher_file()).status_code == 201


def test_otro_residente_de_la_unidad_que_no_registro_el_pago_no_sube(
    actores, pago, subir, voucher_file
):
    response = subir(actores["inquilino"], pago, voucher_file())

    assert response.status_code == 403
    assert not pago.vouchers.exists()


def test_la_junta_no_sube_comprobantes(actores, pago, subir, voucher_file):
    assert subir(actores["junta"], pago, voucher_file()).status_code == 403


def test_el_residente_no_sube_a_un_pago_registrado_por_la_administracion(
    actores, unit, make_payment, usuario, subir, voucher_file
):
    pago = make_payment(unit, source="STAFF", submitted_by=usuario("admin"))

    assert subir(actores["propietario"], pago, voucher_file()).status_code == 403


def test_un_pago_ajeno_da_404_al_subir(actores, pago, subir, voucher_file):
    response = subir(actores["vecino"], pago, voucher_file())

    assert response.status_code == 404
    assert not pago.vouchers.exists()


def test_el_anonimo_no_sube(actores, pago, subir, voucher_file):
    response = subir(actores["anonimo"], pago, voucher_file())

    assert response.status_code in (401, 403)


@pytest.mark.parametrize("estado", ["APPROVED", "REJECTED"])
def test_solo_se_sube_a_un_pago_pendiente(
    actores, unit, make_payment, usuario, subir, voucher_file, estado
):
    pago = make_payment(unit, status=estado, submitted_by=usuario("propietario"))

    response = subir(actores["propietario"], pago, voucher_file())

    assert response.status_code == 409
    assert response.json()["code"] == "payment_not_pending"


def test_una_firma_falsa_da_400_invalid_voucher(actores, pago, subir):
    response = subir(
        actores["propietario"], pago, b"<html>no soy una imagen</html>" * 10
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_voucher"
    assert not pago.vouchers.exists()


def test_un_ejecutable_disfrazado_de_imagen_se_rechaza(actores, pago, subir):
    response = subir(
        actores["propietario"], pago, b"MZ\x90\x00" + b"\x00" * 200, "foto.jpg"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_voucher"


def test_un_archivo_de_mas_de_5_mb_da_400_invalid_voucher(
    actores, pago, subir, voucher_file
):
    grande = voucher_file("jpg", size=MAX_VOUCHER_SIZE + 1)

    response = subir(actores["propietario"], pago, grande)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_voucher"
    assert not pago.vouchers.exists()


def test_un_archivo_de_exactamente_5_mb_se_acepta(actores, pago, subir, voucher_file):
    exacto = voucher_file("jpg", size=MAX_VOUCHER_SIZE)

    assert subir(actores["propietario"], pago, exacto).status_code == 201


def test_un_archivo_vacio_da_400(actores, pago, subir):
    response = subir(actores["propietario"], pago, b"")

    assert response.status_code == 400


def test_sin_archivo_da_400_validation_error(actores, pago):
    response = actores["propietario"].post(url(pago), {}, format="multipart")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "file" in response.json()["errors"]


def test_un_cuerpo_json_no_es_una_subida_valida(actores, pago):
    response = actores["propietario"].post(url(pago), {"file": "x"}, format="json")

    assert response.status_code == 415


def test_el_cuarto_comprobante_da_409_voucher_limit(
    actores, pago, subir, voucher_file, settings
):
    settings.VOUCHER_UPLOAD_RATE = "100/hour"
    for _ in range(3):
        assert subir(actores["propietario"], pago, voucher_file()).status_code == 201

    response = subir(actores["propietario"], pago, voucher_file())

    assert response.status_code == 409
    assert response.json()["code"] == "voucher_limit"
    assert pago.vouchers.count() == 3


def test_el_mismo_archivo_dos_veces_en_un_pago_da_400(
    actores, pago, subir, voucher_file
):
    contenido = voucher_file()
    subir(actores["propietario"], pago, contenido)

    response = subir(actores["propietario"], pago, contenido)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_voucher"


def test_avisa_sin_revelar_si_el_archivo_ya_esta_en_un_pago_ajeno(
    actores, unit2, make_payment, pago, subir, voucher_file
):
    contenido = voucher_file()
    ajeno = make_payment(unit2)
    services.attach_voucher(ajeno, contenido)

    response = subir(actores["propietario"], pago, contenido)

    assert response.status_code == 201
    (aviso,) = response.json()["warnings"]
    assert aviso["code"] == "duplicate_voucher"
    assert set(aviso) == {"code", "detail"}
    assert str(ajeno.pk) not in response.content.decode()


def test_la_gestion_ve_en_que_pagos_esta_el_archivo_repetido(
    actores, unit2, make_payment, pago, subir, voucher_file
):
    contenido = voucher_file()
    ajeno = make_payment(unit2)
    services.attach_voucher(ajeno, contenido)

    (aviso,) = subir(actores["tesorero"], pago, contenido).json()["warnings"]

    assert aviso["payment_ids"] == [str(ajeno.pk)]


# --- Límite de subidas --------------------------------------------------------


def test_se_limitan_las_subidas_por_usuario(
    actores, unit, make_payment, usuario, subir, voucher_file, settings
):
    settings.VOUCHER_UPLOAD_RATE = "2/hour"
    pagos = [make_payment(unit, submitted_by=usuario("propietario")) for _ in range(3)]
    cliente = actores["propietario"]
    assert subir(cliente, pagos[0], voucher_file()).status_code == 201
    assert subir(cliente, pagos[1], voucher_file()).status_code == 201

    response = subir(cliente, pagos[2], voucher_file())

    assert response.status_code == 429
    assert response.json()["code"] == "throttled"
    assert "Retry-After" in response
    assert not pagos[2].vouchers.exists()


def test_el_limite_es_por_usuario_y_no_global(
    actores, pago, unit, make_payment, subir, voucher_file, settings
):
    settings.VOUCHER_UPLOAD_RATE = "1/hour"
    assert subir(actores["propietario"], pago, voucher_file()).status_code == 201

    otro = make_payment(unit)

    assert subir(actores["tesorero"], otro, voucher_file()).status_code == 201


def test_el_limite_viene_de_la_configuracion(
    actores, pago, subir, voucher_file, settings
):
    settings.VOUCHER_UPLOAD_RATE = "1/hour"
    subir(actores["propietario"], pago, voucher_file())

    assert subir(actores["propietario"], pago, voucher_file()).status_code == 429


def test_el_limite_por_defecto_son_20_por_hora():
    from django.conf import settings

    assert getattr(settings, "VOUCHER_UPLOAD_RATE", None) == "20/hour"


def test_descargar_no_cuenta_para_el_limite(actores, pago, voucher_file, settings):
    settings.VOUCHER_UPLOAD_RATE = "1/hour"
    services.attach_voucher(pago, voucher_file())

    for _ in range(3):
        assert actores["propietario"].get(url(pago)).status_code == 200


# --- Descarga -----------------------------------------------------------------


@pytest.fixture
def con_comprobante(pago, voucher_file):
    contenido = voucher_file("jpg")
    adjunto = services.attach_voucher(pago, contenido)
    return pago, adjunto.voucher, contenido


@pytest.mark.parametrize(
    "rol", ["admin", "admin_rol", "tesorero", "propietario", "inquilino"]
)
def test_descarga_el_comprobante_quien_corresponde(actores, con_comprobante, rol):
    pago, _, contenido = con_comprobante

    response = actores[rol].get(url(pago))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == contenido


def test_la_descarga_lleva_las_cabeceras_de_seguridad(actores, con_comprobante):
    pago, _, _ = con_comprobante

    response = actores["propietario"].get(url(pago))

    assert response["Content-Type"] == "image/jpeg"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private"
    assert response["Content-Disposition"] == 'attachment; filename="comprobante-1.jpg"'


def test_el_nombre_de_descarga_no_revela_el_nombre_guardado(actores, con_comprobante):
    pago, voucher, _ = con_comprobante
    guardado = voucher.file.name.rsplit("/", 1)[-1].split(".")[0]

    response = actores["admin"].get(url(pago))

    assert guardado not in response["Content-Disposition"]


@pytest.mark.parametrize(
    ("tipo", "extension"),
    [("png", "png"), ("webp", "webp"), ("pdf", "pdf")],
)
def test_el_nombre_y_el_tipo_de_descarga_siguen_al_archivo(
    actores, pago, voucher_file, tipo, extension
):
    services.attach_voucher(pago, voucher_file(tipo))

    response = actores["admin"].get(url(pago))

    assert f'filename="comprobante-1.{extension}"' in response["Content-Disposition"]


def test_por_defecto_baja_el_comprobante_mas_reciente(actores, pago, voucher_file):
    primero = voucher_file("jpg")
    segundo = voucher_file("png")
    services.attach_voucher(pago, primero)
    services.attach_voucher(pago, segundo)

    response = actores["admin"].get(url(pago))

    assert b"".join(response.streaming_content) == segundo
    assert response["Content-Disposition"].endswith('comprobante-2.png"')


def test_el_parametro_voucher_elige_el_comprobante(actores, pago, voucher_file):
    primero = voucher_file("jpg")
    adjunto = services.attach_voucher(pago, primero)
    services.attach_voucher(pago, voucher_file("png"))

    response = actores["admin"].get(url(pago), {"voucher": str(adjunto.voucher.pk)})

    assert b"".join(response.streaming_content) == primero
    assert response["Content-Disposition"].endswith('comprobante-1.jpg"')


def test_un_voucher_mal_formado_da_400(actores, con_comprobante):
    pago, _, _ = con_comprobante

    response = actores["admin"].get(url(pago), {"voucher": "xyz"})

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_un_voucher_de_otro_pago_da_404(
    actores, con_comprobante, unit2, make_payment, voucher_file
):
    pago, _, _ = con_comprobante
    otro = make_payment(unit2)
    ajeno = services.attach_voucher(otro, voucher_file()).voucher

    response = actores["admin"].get(url(pago), {"voucher": str(ajeno.pk)})

    assert response.status_code == 404


def test_un_voucher_inexistente_da_404(actores, con_comprobante):
    pago, _, _ = con_comprobante

    response = actores["admin"].get(url(pago), {"voucher": str(uuid.uuid4())})

    assert response.status_code == 404


def test_un_pago_sin_comprobantes_da_404(actores, pago):
    assert actores["admin"].get(url(pago)).status_code == 404


def test_la_junta_no_descarga_comprobantes(actores, con_comprobante):
    pago, _, _ = con_comprobante

    assert actores["junta"].get(url(pago)).status_code == 403


def test_el_comprobante_de_un_pago_ajeno_da_404(actores, con_comprobante):
    pago, _, _ = con_comprobante

    assert actores["vecino"].get(url(pago)).status_code == 404


def test_el_vecino_no_baja_el_voucher_de_otro_ni_pasandolo_por_parametro(
    actores, unit2, make_payment, voucher_file, con_comprobante
):
    _, voucher, _ = con_comprobante
    propio = make_payment(unit2)
    services.attach_voucher(propio, voucher_file())

    response = actores["vecino"].get(url(propio), {"voucher": str(voucher.pk)})

    assert response.status_code == 404


def test_el_anonimo_no_descarga(actores, con_comprobante):
    pago, _, _ = con_comprobante

    assert actores["anonimo"].get(url(pago)).status_code in (401, 403)


@pytest.mark.parametrize("estado", ["REJECTED", "APPROVED", "VOID"])
def test_el_comprobante_se_descarga_en_cualquier_estado(
    actores, unit, make_payment, voucher_file, estado
):
    pago = make_payment(unit)
    services.attach_voucher(pago, voucher_file())
    models.Payment.objects.filter(pk=pago.pk).update(
        status=estado, rejection_reason="x", reviewed_at="2025-03-02T10:00:00Z"
    )

    assert actores["admin"].get(url(pago)).status_code == 200


def test_un_tipo_alterado_en_la_bd_se_sirve_como_binario(actores, con_comprobante):
    pago, voucher, _ = con_comprobante
    models.PaymentVoucher.objects.filter(pk=voucher.pk).update(content_type="text/html")

    response = actores["admin"].get(url(pago))

    assert response["Content-Type"] == "application/octet-stream"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "html" not in response["Content-Disposition"]


def test_si_el_archivo_falta_en_disco_da_404_sin_detalles(
    actores, con_comprobante, voucher_root
):
    pago, _, _ = con_comprobante
    for archivo in voucher_root.rglob("*.jpg"):
        archivo.unlink()

    response = actores["admin"].get(url(pago))

    assert response.status_code == 404
    assert response.json()["code"] == "voucher_file_missing"
    assert str(voucher_root) not in response.content.decode()
