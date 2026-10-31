"""S5-A: PDF reales generados con WeasyPrint (se saltan si no carga; el CI sí los corre)."""

from datetime import date

import pytest

from apps.finances import services

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("pdf_real")]


def es_pdf(respuesta):
    contenido = respuesta.content
    return (
        respuesta.status_code == 200
        and contenido.startswith(b"%PDF-")
        and b"%%EOF" in contenido[-1024:]
        and b"FALSO" not in contenido[:32]
        and len(contenido) > 1500
    )


@pytest.fixture
def pago_aprobado(actores, unit, make_charge, make_payment, admin):
    from apps.properties.models import Person

    Person.objects.filter(full_name="propietario").update(full_name="Rosa Quispe")
    make_charge(unit, date(2025, 1, 1), description="Cuota con ñ y tildes áéíóú")
    pago = make_payment(unit, "80.00", operation_number="BCP98765432")
    services.approve_payment(pago, actor=admin)
    return pago


def test_el_recibo_real_es_un_pdf(actores, pago_aprobado):
    respuesta = actores["propietario"].get(
        f"/api/v1/payments/{pago_aprobado.pk}/receipt/pdf/"
    )

    assert es_pdf(respuesta)


def test_el_recibo_anulado_real_es_un_pdf(actores, pago_aprobado, admin):
    services.void_payment(pago_aprobado, "Pago duplicado", actor=admin)

    respuesta = actores["admin"].get(
        f"/api/v1/payments/{pago_aprobado.pk}/receipt/pdf/"
    )

    assert es_pdf(respuesta)


def test_el_estado_de_cuenta_real_es_un_pdf(actores, pago_aprobado, unit):
    respuesta = actores["junta"].get(
        f"/api/v1/units/{unit.pk}/statement/pdf/?as_of=2025-03-31"
    )

    assert es_pdf(respuesta)


def test_el_estado_de_cuenta_de_una_unidad_sin_cargos_real_es_un_pdf(actores, unit2):
    respuesta = actores["admin"].get(f"/api/v1/units/{unit2.pk}/statement/pdf/")

    assert es_pdf(respuesta)


def test_un_estado_de_cuenta_largo_paginado_real_es_un_pdf(actores, unit, make_charge):
    for mes in range(1, 13):
        for anio in (2023, 2024, 2025):
            make_charge(unit, date(anio, mes, 1))

    respuesta = actores["admin"].get(
        f"/api/v1/units/{unit.pk}/statement/pdf/?as_of=2025-12-31"
    )

    assert es_pdf(respuesta)
