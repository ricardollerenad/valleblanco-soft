"""attach_voucher: tipo real, tamaño, nombre aleatorio, límite y duplicados."""

import hashlib
import io
import json
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import AuditLog
from apps.finances import models, services

pytestmark = pytest.mark.django_db

MAXIMO = 5 * 1024 * 1024


def archivos_guardados(raiz):
    return [ruta for ruta in raiz.rglob("*") if ruta.is_file()] if raiz.exists() else []


@pytest.mark.parametrize(
    ("tipo", "mime", "extension"),
    [
        ("jpg", "image/jpeg", "jpg"),
        ("png", "image/png", "png"),
        ("webp", "image/webp", "webp"),
        ("pdf", "application/pdf", "pdf"),
    ],
)
def test_adjunta_y_guarda_en_el_directorio_privado(
    unit, make_payment, voucher_file, voucher_root, tipo, mime, extension
):
    pago = make_payment(unit)
    contenido = voucher_file(tipo)
    resultado = services.attach_voucher(pago, contenido)
    comprobante = resultado.voucher
    assert comprobante.payment == pago
    assert comprobante.content_type == mime
    assert comprobante.size == len(contenido)
    assert comprobante.sha256 == hashlib.sha256(contenido).hexdigest()
    assert re.fullmatch(
        rf"\d{{4}}/\d{{2}}/[0-9a-f]{{32}}\.{extension}", comprobante.file.name
    )
    assert (voucher_root / comprobante.file.name).read_bytes() == contenido
    assert resultado.duplicate_payment_ids == ()


def test_el_comprobante_nunca_tiene_una_url_publica(unit, make_payment, voucher_file):
    comprobante = services.attach_voucher(
        make_payment(unit), voucher_file("jpg")
    ).voucher
    with pytest.raises(ValueError):
        _ = comprobante.file.url


def test_el_nombre_es_aleatorio_y_no_usa_el_del_cliente(
    unit, make_payment, voucher_file
):
    pago = make_payment(unit)
    subido = SimpleUploadedFile("mi-recibo-privado.jpg", voucher_file("jpg"))
    comprobante = services.attach_voucher(pago, subido).voucher
    assert "recibo" not in comprobante.file.name


def test_el_tipo_se_decide_por_el_contenido_no_por_el_nombre(
    unit, make_payment, voucher_file
):
    subido = SimpleUploadedFile("foto.pdf", voucher_file("png"))
    comprobante = services.attach_voucher(make_payment(unit), subido).voucher
    assert comprobante.content_type == "image/png"
    assert comprobante.file.name.endswith(".png")


@pytest.mark.parametrize(
    "contenido",
    [
        b"esto no es una imagen",
        b"GIF89a" + b"\x00" * 20,
        b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        b"<html><script>alert(1)</script></html>",
        b"MZ\x90\x00" + b"\x00" * 20,
        b"RIFF\x24\x00\x00\x00AVI LIST" + b"\x00" * 20,
        b"\xff\xd8",
        b"%PD",
    ],
)
def test_rechaza_firmas_falsas_aunque_la_extension_sea_jpg(
    unit, make_payment, voucher_root, contenido
):
    pago = make_payment(unit)
    subido = SimpleUploadedFile("comprobante.jpg", contenido)
    with pytest.raises(services.InvalidVoucherError):
        services.attach_voucher(pago, subido)
    assert models.PaymentVoucher.objects.count() == 0
    assert archivos_guardados(voucher_root) == []


def test_rechaza_archivos_vacios(unit, make_payment):
    with pytest.raises(services.InvalidVoucherError):
        services.attach_voucher(make_payment(unit), b"")


def test_rechaza_mas_de_5_mb_y_acepta_exactamente_5_mb(
    unit, make_payment, voucher_file, voucher_root
):
    pago = make_payment(unit)
    with pytest.raises(services.InvalidVoucherError):
        services.attach_voucher(pago, voucher_file("jpg", size=MAXIMO + 1))
    assert archivos_guardados(voucher_root) == []
    resultado = services.attach_voucher(pago, voucher_file("jpg", size=MAXIMO))
    assert resultado.voucher.size == MAXIMO


@pytest.mark.parametrize("contenido", ["texto", None, 123])
def test_rechaza_contenidos_que_no_son_bytes_ni_archivo(unit, make_payment, contenido):
    with pytest.raises(services.InvalidVoucherError):
        services.attach_voucher(make_payment(unit), contenido)


def test_acepta_bytes_y_objetos_tipo_archivo(unit, make_payment, voucher_file):
    pago = make_payment(unit)
    services.attach_voucher(pago, io.BytesIO(voucher_file("png")))
    services.attach_voucher(pago, bytearray(voucher_file("pdf")))
    assert pago.vouchers.count() == 2


def test_un_cuarto_comprobante_se_rechaza(unit, make_payment, voucher_file):
    pago = make_payment(unit)
    for _ in range(3):
        services.attach_voucher(pago, voucher_file("jpg"))
    with pytest.raises(services.VoucherLimitError):
        services.attach_voucher(pago, voucher_file("jpg"))
    assert pago.vouchers.count() == 3


@pytest.mark.parametrize("estado", ["APPROVED", "REJECTED", "VOID"])
def test_solo_se_adjunta_mientras_el_pago_esta_pendiente(
    unit, make_payment, voucher_file, estado
):
    pago = make_payment(unit, status=estado)
    with pytest.raises(services.PaymentNotPendingError):
        services.attach_voucher(pago, voucher_file("jpg"))
    assert models.PaymentVoucher.objects.count() == 0


def test_un_sha256_repetido_en_otro_pago_solo_advierte(
    unit, unit2, make_payment, voucher_file
):
    contenido = voucher_file("jpg")
    primero = make_payment(unit)
    segundo = make_payment(unit2)
    services.attach_voucher(primero, contenido)
    resultado = services.attach_voucher(segundo, contenido)
    assert resultado.duplicate_payment_ids == (primero.pk,)
    assert models.PaymentVoucher.objects.count() == 2


def test_el_aviso_de_duplicado_no_cruza_torres(
    unit, other_tower, make_unit, make_payment, voucher_file
):
    contenido = voucher_file("jpg")
    services.attach_voucher(make_payment(unit), contenido)
    ajena = make_unit(other_tower, "101")
    resultado = services.attach_voucher(make_payment(ajena), contenido)
    assert resultado.duplicate_payment_ids == ()


def test_el_mismo_archivo_dos_veces_en_el_mismo_pago_se_rechaza(
    unit, make_payment, voucher_file
):
    pago = make_payment(unit)
    contenido = voucher_file("jpg")
    services.attach_voucher(pago, contenido)
    with pytest.raises(services.InvalidVoucherError):
        services.attach_voucher(pago, contenido)
    assert pago.vouchers.count() == 1


def test_audita_sin_el_contenido_ni_el_nombre_del_archivo(
    unit, admin, make_payment, voucher_file
):
    pago = make_payment(unit, operation_number="0012345678")
    contenido = voucher_file("jpg")
    comprobante = services.attach_voucher(
        pago, contenido, actor=admin, ip="10.0.0.5"
    ).voucher
    evento = AuditLog.objects.get(action="VOUCHER_ATTACHED")
    assert evento.entity_type == "PaymentVoucher"
    assert evento.entity_id == str(comprobante.pk)
    assert evento.actor_id == str(admin.pk)
    assert evento.changes["payment"] == str(pago.pk)
    assert evento.changes["content_type"] == "image/jpeg"
    assert evento.changes["size"] == len(contenido)
    texto = json.dumps(evento.changes)
    assert comprobante.file.name not in texto
    assert "0012345678" not in texto
