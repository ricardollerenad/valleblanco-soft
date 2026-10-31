"""Ayudas comunes de las pruebas de PDF (S5-A)."""

import re

MARCA_ESTILOS = "/* estilos-pdf-valleblanco */"


def texto(respuesta):
    """Contenido de una respuesta como texto (con el renderizador falso es el HTML)."""
    return respuesta.content.decode("utf-8")


def sin_etiquetas(html):
    """Texto visible aproximado: quita etiquetas y estilos y junta los espacios."""
    html = re.sub(r"(?is)<style.*?</style>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()
