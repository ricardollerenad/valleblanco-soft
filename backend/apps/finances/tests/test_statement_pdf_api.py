"""S5-A: `GET /units/{id}/statement/pdf/` (estado de cuenta en PDF)."""

from datetime import date

import pytest

from apps.finances import services

from .utilidades import MARCA_ESTILOS, sin_etiquetas, texto

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("pdf_falso")]

ESPERADO = {
    "anonimo": 403,
    "admin": 200,
    "admin_rol": 200,
    "junta": 200,
    "tesorero": 200,
    "propietario": 200,
    "inquilino": 200,
    "vecino": 404,
}
AS_OF = "2025-03-31"


def url(unidad, **parametros):
    consulta = "".join(f"&{k}={v}" for k, v in parametros.items())
    return f"/api/v1/units/{unidad.pk}/statement/pdf/" + (
        "?" + consulta[1:] if consulta else ""
    )


@pytest.fixture
def cuenta(unit, make_charge, make_payment, admin):
    """Enero pagado, febrero y marzo pendientes (vencidos al 31-mar-2025)."""
    make_charge(unit, date(2025, 1, 1), description="CUOTA-ENERO")
    make_charge(unit, date(2025, 2, 1), description="CUOTA-FEBRERO")
    make_charge(unit, date(2025, 3, 1), description="CUOTA-MARZO")
    services.approve_payment(make_payment(unit, "80.00"), actor=admin)
    return unit


@pytest.mark.parametrize("rol", list(ESPERADO))
def test_matriz_de_roles_del_estado_de_cuenta_pdf(actores, cuenta, rol):
    respuesta = actores[rol].get(url(cuenta, as_of=AS_OF))

    assert respuesta.status_code == ESPERADO[rol]


def test_una_unidad_inexistente_responde_404(actores):
    import uuid

    respuesta = actores["admin"].get(f"/api/v1/units/{uuid.uuid4()}/statement/pdf/")

    assert respuesta.status_code == 404


def test_una_unidad_ajena_responde_igual_que_una_inexistente(actores, cuenta):
    import uuid

    ajena = actores["vecino"].get(url(cuenta))
    inexistente = actores["vecino"].get(f"/api/v1/units/{uuid.uuid4()}/statement/pdf/")

    assert ajena.status_code == inexistente.status_code == 404
    assert ajena.content == inexistente.content


def test_cabeceras_de_la_descarga(actores, cuenta):
    respuesta = actores["propietario"].get(url(cuenta, as_of=AS_OF))

    assert respuesta["Content-Type"] == "application/pdf"
    assert respuesta["X-Content-Type-Options"] == "nosniff"
    assert respuesta["Cache-Control"] == "private"
    assert (
        respuesta["Content-Disposition"]
        == f'attachment; filename="estado-de-cuenta-101-{AS_OF}.pdf"'
    )
    assert respuesta.content.startswith(b"%PDF")


@pytest.mark.parametrize("metodo", ["post", "put", "patch", "delete"])
def test_solo_se_permite_get(actores, cuenta, metodo):
    assert getattr(actores["admin"], metodo)(url(cuenta)).status_code == 405


def test_sin_as_of_usa_la_fecha_de_hoy(actores, cuenta):
    from django.utils import timezone

    hoy = timezone.localdate()

    respuesta = actores["admin"].get(url(cuenta))

    assert (
        f"estado-de-cuenta-101-{hoy.isoformat()}.pdf"
        in respuesta["Content-Disposition"]
    )


def test_as_of_mal_formado_responde_400(actores, cuenta):
    respuesta = actores["admin"].get(url(cuenta, as_of="ayer"))

    assert respuesta.status_code == 400
    assert respuesta.json()["code"] == "validation_error"


def test_reutiliza_el_servicio_unit_statement_con_as_of(actores, cuenta, monkeypatch):
    llamadas = []
    original = services.unit_statement

    def espia(unidad, as_of=None):
        llamadas.append((unidad.pk, as_of))
        return original(unidad, as_of)

    monkeypatch.setattr(services, "unit_statement", espia)

    actores["admin"].get(url(cuenta, as_of=AS_OF))

    assert llamadas == [(cuenta.pk, date(2025, 3, 31))]


def test_el_pdf_trae_el_resumen_y_las_lineas(actores, cuenta):
    visible = sin_etiquetas(texto(actores["propietario"].get(url(cuenta, as_of=AS_OF))))

    assert "Estado de cuenta" in visible
    assert "101" in visible
    assert "Torre 2" in visible
    assert "31/03/2025" in visible
    assert "CUOTA-ENERO" in visible
    assert "CUOTA-FEBRERO" in visible
    assert "CUOTA-MARZO" in visible
    assert "Saldo pendiente" in visible
    assert "S/ 160.00" in visible
    assert "Saldo vencido" in visible
    assert "Meses de deuda" in visible
    assert "Al día" in visible


def test_un_moroso_se_indica_como_tal(actores, unit, make_charge):
    for mes in (1, 2, 3, 4):
        make_charge(unit, date(2025, mes, 1))

    visible = sin_etiquetas(texto(actores["admin"].get(url(unit, as_of="2025-06-30"))))

    assert "Moroso" in visible
    assert "Al día" not in visible


def test_el_saldo_a_favor_se_muestra(actores, unit, make_payment, admin):
    services.approve_payment(make_payment(unit, "50.00"), actor=admin)

    visible = sin_etiquetas(texto(actores["admin"].get(url(unit, as_of=AS_OF))))

    assert "Saldo a favor" in visible
    assert "S/ 50.00" in visible


def test_un_cargo_anulado_no_aparece(actores, unit, make_charge, admin):
    make_charge(unit, date(2025, 1, 1), description="CARGO-VIGENTE")
    anulado = make_charge(unit, date(2025, 2, 1), description="CARGO-ANULADO")
    services.void_charge(anulado, "Error de carga", actor=admin)

    html = texto(actores["admin"].get(url(unit, as_of=AS_OF)))

    assert "CARGO-VIGENTE" in html
    assert "CARGO-ANULADO" not in html


def test_los_cargos_vencidos_se_senalan(actores, cuenta):
    visible = sin_etiquetas(texto(actores["admin"].get(url(cuenta, as_of=AS_OF))))

    assert "Vencido" in visible


def test_una_unidad_sin_cargos_indica_que_no_hay_movimientos(actores, unit2):
    visible = sin_etiquetas(texto(actores["admin"].get(url(unit2, as_of=AS_OF))))

    assert "Sin cargos registrados" in visible


def test_el_html_escapa_el_texto_de_los_cargos(actores, unit, make_charge):
    make_charge(unit, date(2025, 1, 1), description="<img src=x onerror=alert(1)>")

    html = texto(actores["admin"].get(url(unit, as_of=AS_OF)))

    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_usa_la_misma_hoja_de_estilos_que_el_recibo(actores, cuenta):
    assert MARCA_ESTILOS in texto(actores["admin"].get(url(cuenta, as_of=AS_OF)))


def test_pedir_el_pdf_no_modifica_datos(actores, cuenta):
    from apps.core.models import AuditLog
    from apps.finances.models import Charge, Payment

    antes = (Charge.objects.count(), Payment.objects.count(), AuditLog.objects.count())

    actores["admin"].get(url(cuenta, as_of=AS_OF))

    assert antes == (
        Charge.objects.count(),
        Payment.objects.count(),
        AuditLog.objects.count(),
    )


def test_si_no_hay_renderizador_responde_503(actores, cuenta, monkeypatch):
    from apps.finances import pdf

    def sin_pdf(html):
        raise pdf.PdfNoDisponibleError("sin WeasyPrint")

    monkeypatch.setattr(pdf, "render_pdf", sin_pdf)

    respuesta = actores["admin"].get(url(cuenta))

    assert respuesta.status_code == 503
    assert respuesta.json()["code"] == "pdf_unavailable"
