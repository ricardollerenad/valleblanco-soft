"""Límite de subidas de comprobantes por usuario."""

from django.conf import settings
from rest_framework.throttling import UserRateThrottle

DEFAULT_VOUCHER_UPLOAD_RATE = "20/hour"


class VoucherUploadThrottle(UserRateThrottle):
    """Limita cuántas veces por hora un usuario puede subir comprobantes.

    La tasa sale de `settings.VOUCHER_UPLOAD_RATE` (p. ej. `20/hour`) y se lee en
    cada uso, así se puede cambiar por entorno y en las pruebas.
    """

    scope = "voucher_upload"

    def get_rate(self):
        return getattr(settings, "VOUCHER_UPLOAD_RATE", DEFAULT_VOUCHER_UPLOAD_RATE)
