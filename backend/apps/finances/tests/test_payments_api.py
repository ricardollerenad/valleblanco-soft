"""S4-B: GET/POST /payments/ y GET /payments/{id}/."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.finances import models, services

pytestmark = pytest.mark.django_db

LISTA = "/api/v1/payments/"
GESTION = ("admin", "admin_rol", "junta", "tesorero")
CAMPOS = {
    "id",
    "unit",
    "unit_code",
    "amount",
    "method",
    "operation_number",
    "paid_at",
    "status",
    "source",
    "rejection_reason",
    "vouchers",
    "allocated_amount",
    "credit_remainder",
    "created_at",
}


def detalle(pago):
    return f"/api/v1/payments/{pago.pk}/"


@pytest.fixture
def dos_pagos(unit, unit2, make_payment):
    """Un pago de la 101 (propietario e inquilino) y otro de la 102 (vecino)."""
    return make_payment(unit), make_payment(unit2)


# --- Listado ------------------------------------------------------------------


@pytest.mark.parametrize("rol", GESTION)
def test_la_gestion_ve_los_pagos_de_todas_las_unidades(actores, dos_pagos, rol):
    response = actores[rol].get(LISTA)

    assert response.status_code == 200
    assert response.json()["count"] == 2


@pytest.mark.parametrize("rol", ["propietario", "inquilino"])
def test_un_residente_ve_solo_los_pagos_de_su_unidad(actores, dos_pagos, rol):
    datos = actores[rol].get(LISTA).json()

    assert datos["count"] == 1
    assert datos["results"][0]["id"] == str(dos_pagos[0].pk)


def test_el_vecino_ve_solo_su_pago(actores, dos_pagos):
    datos = actores["vecino"].get(LISTA).json()

    assert [p["id"] for p in datos["results"]] == [str(dos_pagos[1].pk)]


def test_el_anonimo_no_ve_pagos(actores, dos_pagos):
    assert actores["anonimo"].get(LISTA).status_code in (401, 403)


def test_el_residente_ve_sus_pagos_rechazados_con_el_motivo(
    actores, unit, make_payment
):
    make_payment(unit, status="REJECTED", rejection_reason="Monto distinto")

    (pago,) = actores["propietario"].get(LISTA).json()["results"]

    assert pago["status"] == "REJECTED"
    assert pago["rejection_reason"] == "Monto distinto"


def test_un_ocupante_que_ya_no_vive_en_la_unidad_no_ve_sus_pagos(
    actores, unit, make_payment, usuario
):
    make_payment(unit)
    unit.occupancies.filter(person__user=usuario("propietario")).update(
        end_date=date(2025, 1, 1)
    )

    assert actores["propietario"].get(LISTA).json()["count"] == 0


def test_filtra_por_estado(actores, unit, make_payment):
    make_payment(unit, status="PENDING")
    make_payment(unit, status="REJECTED")

    datos = actores["admin"].get(LISTA, {"status": "REJECTED"}).json()

    assert [p["status"] for p in datos["results"]] == ["REJECTED"]


def test_filtra_por_medio(actores, unit, make_payment):
    make_payment(unit, method="YAPE")
    make_payment(unit, method="PLIN")

    datos = actores["admin"].get(LISTA, {"method": "PLIN"}).json()

    assert [p["method"] for p in datos["results"]] == ["PLIN"]


def test_filtra_por_rango_de_fechas(actores, unit, make_payment):
    make_payment(unit, paid_at=date(2025, 1, 10))
    make_payment(unit, paid_at=date(2025, 2, 10))
    make_payment(unit, paid_at=date(2025, 3, 10))

    datos = actores["admin"].get(
        LISTA, {"paid_from": "2025-02-01", "paid_to": "2025-02-28"}
    )

    assert [p["paid_at"] for p in datos.json()["results"]] == ["2025-02-10"]


def test_las_fechas_del_filtro_incluyen_los_extremos(actores, unit, make_payment):
    make_payment(unit, paid_at=date(2025, 2, 1))
    make_payment(unit, paid_at=date(2025, 2, 28))

    datos = actores["admin"].get(
        LISTA, {"paid_from": "2025-02-01", "paid_to": "2025-02-28"}
    )

    assert datos.json()["count"] == 2


def test_filtra_por_unidad(actores, dos_pagos, unit2):
    datos = actores["admin"].get(LISTA, {"unit": str(unit2.pk)}).json()

    assert [p["id"] for p in datos["results"]] == [str(dos_pagos[1].pk)]


def test_filtrar_por_una_unidad_ajena_da_404(actores, dos_pagos, unit):
    response = actores["vecino"].get(LISTA, {"unit": str(unit.pk)})

    assert response.status_code == 404


def test_filtrar_por_una_unidad_inexistente_da_404_incluso_a_la_gestion(
    actores, dos_pagos
):
    import uuid

    response = actores["admin"].get(LISTA, {"unit": str(uuid.uuid4())})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "parametros",
    [
        {"unit": "no-es-uuid"},
        {"status": "INVENTADO"},
        {"method": "EFECTIVO"},
        {"paid_from": "ayer"},
        {"paid_to": "2025-13-45"},
        {"paid_from": "2025-03-10", "paid_to": "2025-03-01"},
    ],
)
def test_los_filtros_mal_formados_dan_400(actores, dos_pagos, parametros):
    response = actores["admin"].get(LISTA, parametros)

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_el_listado_se_pagina_de_25_en_25(actores, unit, make_payment):
    for _ in range(30):
        make_payment(unit)

    datos = actores["admin"].get(LISTA).json()

    assert datos["count"] == 30
    assert len(datos["results"]) == 25
    assert datos["next"] is not None


def test_el_listado_no_hace_una_consulta_por_pago(
    actores, unit, make_payment, voucher_file
):
    def consultas(cantidad):
        for _ in range(cantidad):
            pago = make_payment(unit)
            services.attach_voucher(pago, voucher_file())
        with CaptureQueriesContext(connection) as capturadas:
            assert actores["junta"].get(LISTA).status_code == 200
        return len(capturadas)

    pocas = consultas(2)
    muchas = consultas(8)

    assert muchas == pocas


# --- Detalle ------------------------------------------------------------------


@pytest.mark.parametrize("rol", [*GESTION, "propietario", "inquilino"])
def test_ve_el_detalle_de_un_pago_visible(actores, dos_pagos, rol):
    response = actores[rol].get(detalle(dos_pagos[0]))

    assert response.status_code == 200
    assert response.json()["id"] == str(dos_pagos[0].pk)


def test_el_pago_ajeno_da_404(actores, dos_pagos):
    assert actores["vecino"].get(detalle(dos_pagos[0])).status_code == 404
    assert actores["propietario"].get(detalle(dos_pagos[1])).status_code == 404


def test_un_pago_inexistente_da_404(actores, dos_pagos):
    import uuid

    response = actores["admin"].get(f"/api/v1/payments/{uuid.uuid4()}/")

    assert response.status_code == 404


def test_el_detalle_trae_exactamente_los_campos_acordados(actores, dos_pagos):
    datos = actores["admin"].get(detalle(dos_pagos[0])).json()

    assert set(datos) == CAMPOS


def test_el_dinero_sale_como_texto_con_dos_decimales(actores, unit, make_payment):
    pago = make_payment(unit, "80.5")

    datos = actores["admin"].get(detalle(pago)).json()

    assert datos["amount"] == "80.50"
    assert datos["allocated_amount"] == "0.00"
    assert datos["credit_remainder"] == "0.00"
    assert datos["unit_code"] == "101"
    assert datos["unit"] == str(unit.pk)


@pytest.mark.parametrize("rol", ["admin", "tesorero", "junta"])
def test_la_gestion_ve_el_numero_de_operacion_completo(
    actores, unit, make_payment, rol
):
    pago = make_payment(unit, operation_number="AB12345678")

    datos = actores[rol].get(detalle(pago)).json()

    assert datos["operation_number"] == "AB12345678"


def test_quien_registro_el_pago_ve_el_numero_completo(
    actores, unit, make_payment, usuario
):
    pago = make_payment(
        unit, operation_number="AB12345678", submitted_by=usuario("inquilino")
    )

    datos = actores["inquilino"].get(detalle(pago)).json()

    assert datos["operation_number"] == "AB12345678"


def test_otro_residente_de_la_unidad_ve_el_numero_enmascarado(
    actores, unit, make_payment, usuario
):
    pago = make_payment(
        unit, operation_number="AB12345678", submitted_by=usuario("inquilino")
    )

    datos = actores["propietario"].get(detalle(pago)).json()

    assert datos["operation_number"] == "****5678"


def test_un_pago_sin_numero_de_operacion_no_se_enmascara(actores, unit, make_payment):
    pago = make_payment(unit, method="LEGACY", operation_number="", source="IMPORT")

    datos = actores["propietario"].get(detalle(pago)).json()

    assert datos["operation_number"] == ""


def test_el_listado_tambien_enmascara_el_numero_a_quien_no_lo_registro(
    actores, unit, make_payment, usuario
):
    make_payment(unit, operation_number="AB12345678", submitted_by=usuario("inquilino"))

    (pago,) = actores["propietario"].get(LISTA).json()["results"]

    assert pago["operation_number"] == "****5678"


def test_un_pago_aprobado_muestra_lo_aplicado_y_el_saldo_a_favor(
    actores, unit, make_charge, pay
):
    make_charge(unit, date(2025, 3, 1), "80.00")
    pago = pay(unit, "100.00")

    datos = actores["admin"].get(detalle(pago)).json()

    assert datos["allocated_amount"] == "80.00"
    assert datos["credit_remainder"] == "20.00"


def test_un_pago_anulado_ya_no_aplica_ni_deja_saldo(actores, unit, make_charge, pay):
    make_charge(unit, date(2025, 3, 1), "80.00")
    pago = pay(unit, "100.00")
    services.void_payment(pago, "Error del banco")

    datos = actores["admin"].get(detalle(pago)).json()

    assert datos["status"] == "VOID"
    assert datos["allocated_amount"] == "0.00"
    assert datos["credit_remainder"] == "0.00"


def test_un_pago_pendiente_no_aplica_nada(actores, unit, make_charge, make_payment):
    make_charge(unit, date(2025, 3, 1), "80.00")
    pago = make_payment(unit, "100.00")

    datos = actores["admin"].get(detalle(pago)).json()

    assert datos["allocated_amount"] == "0.00"
    assert datos["credit_remainder"] == "0.00"


def test_los_comprobantes_solo_exponen_id_tipo_tamano_y_fecha(
    actores, unit, make_payment, voucher_file
):
    pago = make_payment(unit)
    contenido = voucher_file("png")
    services.attach_voucher(pago, contenido)

    (voucher,) = actores["admin"].get(detalle(pago)).json()["vouchers"]

    assert set(voucher) == {"id", "content_type", "size", "created_at"}
    assert voucher["content_type"] == "image/png"
    assert voucher["size"] == len(contenido)


# --- Registro -----------------------------------------------------------------


@pytest.mark.parametrize("rol", ["propietario", "inquilino"])
def test_un_residente_registra_un_pago_de_su_unidad(
    actores, unit, datos_pago, usuario, rol
):
    response = actores[rol].post(LISTA, datos_pago(unit), format="json")

    assert response.status_code == 201
    pago = models.Payment.objects.get()
    assert pago.status == "PENDING"
    assert pago.source == "RESIDENT"
    assert pago.submitted_by == usuario(rol)
    assert pago.unit == unit
    assert pago.amount == Decimal("80.00")


def test_la_respuesta_del_registro_trae_el_pago_y_las_advertencias(
    actores, unit, datos_pago
):
    datos = actores["propietario"].post(LISTA, datos_pago(unit), format="json").json()

    assert set(datos) == CAMPOS | {"warnings"}
    assert datos["status"] == "PENDING"
    assert datos["operation_number"] == "OP0001"
    assert datos["warnings"] == []


@pytest.mark.parametrize("rol", ["admin", "admin_rol", "tesorero"])
def test_administracion_y_tesoreria_registran_a_nombre_de_cualquier_unidad(
    actores, unit2, datos_pago, rol
):
    response = actores[rol].post(LISTA, datos_pago(unit2), format="json")

    assert response.status_code == 201
    pago = models.Payment.objects.get()
    assert pago.source == "STAFF"
    assert pago.unit == unit2


def test_la_junta_sin_unidad_no_registra_pagos(actores, unit, datos_pago):
    response = actores["junta"].post(LISTA, datos_pago(unit), format="json")

    assert response.status_code == 404
    assert not models.Payment.objects.exists()


def test_un_residente_no_registra_en_una_unidad_ajena(actores, unit, datos_pago):
    response = actores["vecino"].post(LISTA, datos_pago(unit), format="json")

    assert response.status_code == 404
    assert not models.Payment.objects.exists()


def test_registrar_en_una_unidad_inexistente_da_404(actores, datos_pago, unit):
    import uuid

    datos = datos_pago(unit, unit=str(uuid.uuid4()))

    assert actores["admin"].post(LISTA, datos, format="json").status_code == 404


def test_el_anonimo_no_registra_pagos(actores, unit, datos_pago):
    response = actores["anonimo"].post(LISTA, datos_pago(unit), format="json")

    assert response.status_code in (401, 403)


def test_siempre_nace_pendiente_aunque_el_cuerpo_diga_otra_cosa(
    actores, unit, datos_pago
):
    datos = datos_pago(unit, status="APPROVED", source="IMPORT", reviewed_by="x")

    response = actores["propietario"].post(LISTA, datos, format="json")

    assert response.status_code == 201
    pago = models.Payment.objects.get()
    assert (pago.status, pago.source, pago.reviewed_by) == (
        "PENDING",
        "RESIDENT",
        None,
    )


def test_el_medio_legacy_no_se_acepta_por_la_api(actores, unit, datos_pago):
    response = actores["admin"].post(
        LISTA, datos_pago(unit, method="LEGACY"), format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "method" in response.json()["errors"]


@pytest.mark.parametrize("medio", ["TRANSFER", "DEPOSIT", "YAPE", "PLIN"])
def test_acepta_los_cuatro_medios_de_pago(actores, unit, datos_pago, medio):
    response = actores["propietario"].post(
        LISTA, datos_pago(unit, method=medio), format="json"
    )

    assert response.status_code == 201


def test_no_acepta_efectivo(actores, unit, datos_pago):
    response = actores["propietario"].post(
        LISTA, datos_pago(unit, method="CASH"), format="json"
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "campo", ["unit", "amount", "method", "operation_number", "paid_at"]
)
def test_todos_los_campos_son_obligatorios(actores, unit, datos_pago, campo):
    datos = datos_pago(unit)
    del datos[campo]

    response = actores["admin"].post(LISTA, datos, format="json")

    assert response.status_code == 400
    assert campo in response.json()["errors"]


def test_una_unidad_mal_formada_da_400(actores, unit, datos_pago):
    response = actores["admin"].post(LISTA, datos_pago(unit, unit="101"), format="json")

    assert response.status_code == 400
    assert "unit" in response.json()["errors"]


@pytest.mark.parametrize("monto", ["0", "-5.00", "abc", "80.123"])
def test_un_monto_invalido_da_400_invalid_payment(actores, unit, datos_pago, monto):
    response = actores["propietario"].post(
        LISTA, datos_pago(unit, amount=monto), format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_payment"


def test_una_fecha_futura_da_400_invalid_payment(actores, unit, datos_pago):
    response = actores["propietario"].post(
        LISTA, datos_pago(unit, paid_at="2999-01-01"), format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_payment"


def test_una_fecha_mal_formada_da_400_validation_error(actores, unit, datos_pago):
    response = actores["propietario"].post(
        LISTA, datos_pago(unit, paid_at="ayer"), format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_un_numero_de_operacion_vacio_da_400(actores, unit, datos_pago):
    response = actores["propietario"].post(
        LISTA, datos_pago(unit, operation_number="  "), format="json"
    )

    assert response.status_code == 400


def test_registrar_en_una_unidad_inactiva_da_400_invalid_payment(
    actores, unit, datos_pago
):
    unit.is_active = False
    unit.save()

    response = actores["admin"].post(LISTA, datos_pago(unit), format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_payment"


def test_el_numero_de_operacion_se_guarda_normalizado(actores, unit, datos_pago):
    response = actores["propietario"].post(
        LISTA, datos_pago(unit, operation_number=" op-00 12 "), format="json"
    )

    assert response.json()["operation_number"] == "OP0012"
    assert models.Payment.objects.get().operation_number == "OP0012"


def test_un_pago_duplicado_da_409_duplicate_payment(
    actores, unit, datos_pago, make_payment
):
    make_payment(unit, "80.00", method="YAPE", operation_number="OP0001")

    response = actores["propietario"].post(LISTA, datos_pago(unit), format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_payment"
    assert models.Payment.objects.count() == 1


def test_el_duplicado_de_otra_unidad_no_revela_nada_al_residente(
    actores, unit, unit2, datos_pago, make_payment
):
    otro = make_payment(unit2, "80.00", method="YAPE", operation_number="OP0001")

    response = actores["propietario"].post(LISTA, datos_pago(unit), format="json")

    assert response.status_code == 409
    assert response.json()["errors"] == {}
    assert str(otro.pk) not in response.content.decode()


def test_el_duplicado_le_dice_a_la_gestion_cual_es_el_pago_existente(
    actores, unit, unit2, datos_pago, make_payment
):
    otro = make_payment(unit2, "80.00", method="YAPE", operation_number="OP0001")

    response = actores["tesorero"].post(LISTA, datos_pago(unit), format="json")

    assert response.status_code == 409
    assert response.json()["errors"] == {"existing_payment": str(otro.pk)}


def test_un_pago_rechazado_no_cuenta_como_duplicado(
    actores, unit, datos_pago, make_payment
):
    make_payment(
        unit, "80.00", method="YAPE", operation_number="OP0001", status="REJECTED"
    )

    response = actores["propietario"].post(LISTA, datos_pago(unit), format="json")

    assert response.status_code == 201


def test_avisa_si_el_numero_de_operacion_ya_existe_con_otro_monto(
    actores, unit, unit2, datos_pago, make_payment
):
    make_payment(unit2, "50.00", method="YAPE", operation_number="OP0001")

    datos = actores["propietario"].post(LISTA, datos_pago(unit), format="json").json()

    assert [w["code"] for w in datos["warnings"]] == ["same_operation_number"]
    assert set(datos["warnings"][0]) == {"code", "detail"}


def test_la_gestion_ve_que_pagos_comparten_el_numero_de_operacion(
    actores, unit, unit2, datos_pago, make_payment
):
    otro = make_payment(unit2, "50.00", method="YAPE", operation_number="OP0001")

    datos = actores["admin"].post(LISTA, datos_pago(unit), format="json").json()

    assert datos["warnings"][0]["payment_ids"] == [str(otro.pk)]


def test_no_avisa_si_el_numero_de_operacion_es_de_otro_medio(
    actores, unit, unit2, datos_pago, make_payment
):
    make_payment(unit2, "50.00", method="PLIN", operation_number="OP0001")

    datos = actores["propietario"].post(LISTA, datos_pago(unit), format="json").json()

    assert datos["warnings"] == []
