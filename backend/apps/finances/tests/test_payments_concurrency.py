"""Concurrencia real: solo con PostgreSQL (SQLite no implementa select_for_update)."""

import threading
from datetime import date
from decimal import Decimal

import pytest
from django.db import connection, connections

from apps.finances import models, services


def en_paralelo(tareas):
    """Ejecuta cada tarea en su hilo y conexión; devuelve las excepciones."""
    barrera = threading.Barrier(len(tareas))
    errores = []

    def correr(tarea):
        try:
            barrera.wait(timeout=10)
            tarea()
        except Exception as error:
            errores.append(error)
        finally:
            connections.close_all()

    hilos = [threading.Thread(target=correr, args=(t,)) for t in tareas]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    return errores


@pytest.mark.django_db(transaction=True)
def test_dos_aprobaciones_simultaneas_no_sobreasignan_el_cargo(
    unit, make_charge, make_payment
):
    if connection.vendor != "postgresql":
        pytest.skip("Requiere PostgreSQL.")
    cargo = make_charge(unit, date(2025, 1, 1))
    primero = make_payment(unit, "80.00")
    segundo = make_payment(unit, "80.00")

    errores = en_paralelo(
        [
            lambda: services.approve_payment(models.Payment.objects.get(pk=primero.pk)),
            lambda: services.approve_payment(models.Payment.objects.get(pk=segundo.pk)),
        ]
    )

    assert errores == []
    total = sum(a.amount for a in models.PaymentAllocation.objects.filter(charge=cargo))
    assert total == Decimal("80.00")
    assert services.credit_balance(unit) == Decimal("80.00")


@pytest.mark.django_db(transaction=True)
def test_aprobar_dos_veces_a_la_vez_el_mismo_pago_solo_aplica_una(
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

    assert [type(e) for e in errores] == [services.PaymentNotPendingError]
    assert models.PaymentAllocation.objects.filter(payment=pago).count() == 1


@pytest.mark.django_db(transaction=True)
def test_dos_registros_simultaneos_del_mismo_pago_solo_dejan_uno(unit, unit2):
    if connection.vendor != "postgresql":
        pytest.skip("Requiere PostgreSQL.")

    def registrar(destino):
        return lambda: services.submit_payment(
            destino, "80.00", "TRANSFER", "OP123", date(2025, 3, 1)
        )

    errores = en_paralelo([registrar(unit), registrar(unit2)])

    assert [type(e) for e in errores] == [services.DuplicatePaymentError]
    assert models.Payment.objects.count() == 1
