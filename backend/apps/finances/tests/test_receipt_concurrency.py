"""Concurrencia real del correlativo: solo con PostgreSQL (SQLite no bloquea filas)."""

from datetime import date

import pytest
from django.db import connection
from django.utils import timezone

from apps.finances import models, services

from .test_payments_concurrency import en_paralelo


def numero(n):
    return f"R-{timezone.localdate().year}-{n:06d}"


@pytest.mark.django_db(transaction=True)
def test_aprobaciones_simultaneas_de_unidades_distintas_no_repiten_numeros(
    tower, make_unit, make_payment
):
    if connection.vendor != "postgresql":
        pytest.skip("Requiere PostgreSQL.")
    unidades = [make_unit(tower, f"20{n}") for n in range(1, 7)]
    pagos = [make_payment(u, "80.00") for u in unidades]

    errores = en_paralelo(
        [
            lambda p=p: services.approve_payment(models.Payment.objects.get(pk=p.pk))
            for p in pagos
        ]
    )

    assert errores == []
    numeros = sorted(models.Receipt.objects.values_list("number", flat=True))
    assert numeros == [numero(n) for n in range(1, 7)]


@pytest.mark.django_db(transaction=True)
def test_aprobar_a_la_vez_el_mismo_pago_emite_un_solo_recibo(
    unit, make_charge, make_payment
):
    if connection.vendor != "postgresql":
        pytest.skip("Requiere PostgreSQL.")
    make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, "80.00")

    errores = en_paralelo(
        [
            lambda: services.approve_payment(models.Payment.objects.get(pk=pago.pk)),
            lambda: services.approve_payment(models.Payment.objects.get(pk=pago.pk)),
        ]
    )

    assert len(errores) == 1
    assert isinstance(errores[0], services.PaymentNotPendingError)
    assert models.Receipt.objects.count() == 1
    assert models.Receipt.objects.get().number == numero(1)


@pytest.mark.django_db(transaction=True)
def test_la_base_impide_numeros_repetidos_aunque_falle_el_contador(unit, make_payment):
    """La restricción única es la última defensa del correlativo."""
    from django.db import IntegrityError

    pago1 = make_payment(unit, status="APPROVED")
    pago2 = make_payment(unit, status="APPROVED")
    models.Receipt.objects.create(
        payment=pago1, number=numero(1), issued_at=timezone.now(), snapshot={}
    )

    with pytest.raises(IntegrityError):
        models.Receipt.objects.create(
            payment=pago2, number=numero(1), issued_at=timezone.now(), snapshot={}
        )
