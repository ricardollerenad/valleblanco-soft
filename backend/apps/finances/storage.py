"""Almacenamiento privado de comprobantes de pago.

Los archivos viven bajo `settings.VOUCHER_ROOT` (en Docker, un volumen nombrado) y
nunca tienen una URL pública: se descargan solo a través de la API, tras verificar
permisos. Por eso `url()` falla a propósito.
"""

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateVoucherStorage(FileSystemStorage):
    """Lee `VOUCHER_ROOT` en cada uso (así las pruebas pueden cambiarlo) y no da URL."""

    def __init__(self):
        super().__init__(file_permissions_mode=0o600, directory_permissions_mode=0o700)

    @property
    def base_location(self):
        return settings.VOUCHER_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError(
            "Los comprobantes son privados: solo se descargan por la API autorizada."
        )


def voucher_storage():
    """Fábrica referenciada por el campo `PaymentVoucher.file` (y su migración)."""
    return PrivateVoucherStorage()
