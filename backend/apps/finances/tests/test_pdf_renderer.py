"""S5-A: renderizador de PDF aislado (`apps.finances.pdf`) y configuración de plantillas."""

import sys

import pytest
from django.conf import settings


def test_settings_define_las_plantillas_de_django():
    plantillas = settings.TEMPLATES

    assert len(plantillas) == 1
    assert plantillas[0]["BACKEND"] == "django.template.backends.django.DjangoTemplates"
    assert plantillas[0]["APP_DIRS"] is True


def test_weasyprint_disponible_devuelve_un_booleano():
    from apps.finances import pdf

    assert isinstance(pdf.weasyprint_disponible(), bool)


def test_si_weasyprint_no_carga_se_informa_y_no_se_renderiza(monkeypatch):
    from apps.finances import pdf

    # `None` en sys.modules hace que `import weasyprint` falle con ImportError.
    monkeypatch.setitem(sys.modules, "weasyprint", None)

    assert pdf.weasyprint_disponible() is False
    with pytest.raises(pdf.PdfNoDisponibleError):
        pdf.render_pdf("<p>hola</p>")


def test_render_pdf_real_produce_un_pdf_valido(pdf_real):
    from apps.finances import pdf

    datos = pdf.render_pdf("<html><body><p>Recibo ñandú S/ 80.00</p></body></html>")

    assert datos.startswith(b"%PDF-")
    assert b"%%EOF" in datos[-1024:]


def test_render_pdf_real_no_descarga_recursos_externos(pdf_real, caplog):
    """Defensa en profundidad: ni imágenes ni hojas de estilo remotas."""
    from apps.finances import pdf

    html = '<html><body><p>x</p><img src="http://127.0.0.1:9/logo.png"></body></html>'

    datos = pdf.render_pdf(html)

    assert datos.startswith(b"%PDF-")
    assert "disallowed protocol" in caplog.text


def test_el_renderizador_se_sustituye_con_una_sola_funcion(pdf_falso):
    from apps.finances import pdf

    assert pdf.render_pdf("<p>hola</p>") == b"%PDF-FALSO\n<p>hola</p>"
