"""S5-A: modelo `Receipt` y contador por año, con restricciones en la base de datos."""

from datetime import date

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture
def crear_recibo(make_payment):
    """Crea un recibo directo en la BD (sin pasar por la aprobación)."""
    from apps.finances.models import Receipt

    def _crear(unit, number="R-2026-000001", **extra):
        pago = extra.pop("payment", None) or make_payment(unit, status="APPROVED")
        extra.setdefault("issued_at", timezone.now())
        extra.setdefault("snapshot", {"version": 1, "number": number})
        return Receipt.objects.create(payment=pago, number=number, **extra)

    return _crear


def test_un_pago_tiene_a_lo_sumo_un_recibo(unit, make_payment, crear_recibo):
    pago = make_payment(unit, status="APPROVED")
    crear_recibo(unit, "R-2026-000001", payment=pago)

    with pytest.raises(IntegrityError), transaction.atomic():
        crear_recibo(unit, "R-2026-000002", payment=pago)


def test_el_numero_de_recibo_es_unico(unit, crear_recibo):
    crear_recibo(unit, "R-2026-000001")

    with pytest.raises(IntegrityError), transaction.atomic():
        crear_recibo(unit, "R-2026-000001")


def test_el_recibo_se_accede_desde_el_pago(unit, crear_recibo):
    recibo = crear_recibo(unit)

    assert recibo.payment.receipt == recibo
    assert str(recibo) == "R-2026-000001"


def test_un_recibo_no_se_modifica(unit, crear_recibo):
    from apps.finances.models import ReceiptImmutableError

    recibo = crear_recibo(unit)
    recibo.snapshot = {"alterado": True}

    with pytest.raises(ReceiptImmutableError):
        recibo.save()


def test_un_recibo_no_se_modifica_en_masa(unit, crear_recibo):
    from apps.finances.models import Receipt, ReceiptImmutableError

    crear_recibo(unit)

    with pytest.raises(ReceiptImmutableError):
        Receipt.objects.update(number="R-2026-999999")


def test_un_recibo_no_se_borra(unit, crear_recibo):
    from apps.finances.models import Receipt, ReceiptImmutableError

    recibo = crear_recibo(unit)

    with pytest.raises(ReceiptImmutableError):
        recibo.delete()
    with pytest.raises(ReceiptImmutableError):
        Receipt.objects.all().delete()
    assert Receipt.objects.count() == 1


def test_el_snapshot_guarda_el_json_tal_cual(unit, crear_recibo):
    recibo = crear_recibo(unit, snapshot={"monto": "80.00", "periodos": [1, 2]})

    recibo.refresh_from_db()

    assert recibo.snapshot == {"monto": "80.00", "periodos": [1, 2]}


def test_el_contador_por_anio_es_unico(db):
    from apps.finances.models import ReceiptCounter

    ReceiptCounter.objects.create(year=2026, last_number=3)

    with pytest.raises(IntegrityError), transaction.atomic():
        ReceiptCounter.objects.create(year=2026)


def test_el_contador_no_acepta_valores_negativos(db):
    from apps.finances.models import ReceiptCounter

    with pytest.raises(IntegrityError), transaction.atomic():
        ReceiptCounter.objects.create(year=2026, last_number=-1)


def test_el_pago_aprobado_no_depende_del_recibo(unit, make_payment):
    """Un pago importado (LEGACY) puede estar aprobado sin recibo."""
    pago = make_payment(
        unit,
        method="LEGACY",
        operation_number="",
        status="APPROVED",
        paid_at=date(2024, 5, 1),
    )

    from apps.finances.models import Receipt

    assert not Receipt.objects.filter(payment=pago).exists()
