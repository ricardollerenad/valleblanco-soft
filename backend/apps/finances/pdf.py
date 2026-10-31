"""Generación de PDF: la ÚNICA función que toca WeasyPrint es `render_pdf`.

Las pruebas la sustituyen por un renderizador falso (en Windows, WeasyPrint exige
GTK); las pruebas con `pdf_real` y el CI en Linux usan la real. Quien necesite un
PDF debe llamar `pdf.render_pdf(html)` (atributo del módulo), nunca importar el
nombre directamente: así la sustitución funciona.
"""

import logging

logger = logging.getLogger(__name__)

# El HTML se arma con datos propios y escapados; aun así no se permite descargar
# nada externo (imágenes, hojas de estilo o fuentes remotas): solo `data:`.
PROTOCOLOS_PERMITIDOS = ("data",)


class PdfNoDisponibleError(Exception):
    """WeasyPrint o sus librerías del sistema (Pango/GTK) no se pueden cargar."""


def weasyprint_disponible():
    """`True` si WeasyPrint carga. En Windows sin GTK lanza OSError al importar."""
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def render_pdf(html):
    """Convierte HTML en los bytes de un PDF."""
    try:
        from weasyprint import HTML, URLFetcher
    except (ImportError, OSError) as error:
        raise PdfNoDisponibleError(
            "WeasyPrint no está disponible: instala sus librerías del sistema."
        ) from error
    fetcher = URLFetcher(allowed_protocols=PROTOCOLOS_PERMITIDOS)
    return HTML(string=html, url_fetcher=fetcher).write_pdf()
