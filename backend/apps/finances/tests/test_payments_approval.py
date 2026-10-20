"""approve_payment y allocated_totals: aplicación al cargo más antiguo primero."""

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.models import AuditLog
from apps.finances import models, services

pytestmark = pytest.mark.django_db

D = Decimal


def asignado(pago, cargo):
    fila = models.PaymentAllocation.objects.filter(payment=pago, charge=cargo).first()
    return fila.amount if fila else None


def test_pago_exacto_cubre_el_cargo(unit, admin, make_charge, make_payment):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, "80.00")
    resultado = services.approve_payment(pago, actor=admin, ip="10.0.0.5")
    pago.refresh_from_db()
    assert pago.status == "APPROVED"
    assert pago.reviewed_by == admin
    assert pago.reviewed_at is not None
    assert resultado.payment.pk == pago.pk
    assert resultado.charges_covered == 1
    assert resultado.surplus == D("0.00")
    assert len(resultado.allocations) == 1
    assert services.pending_amount(cargo) == D("0.00")
    assert services.credit_balance(unit) == D("0.00")
    assert asignado(pago, cargo) == D("80.00")


def test_el_objeto_recibido_queda_actualizado(unit, admin, make_payment):
    pago = make_payment(unit, "80.00")
    services.approve_payment(pago, actor=admin)
    assert pago.status == "APPROVED"
    assert pago.reviewed_by == admin


def test_pago_parcial_deja_saldo_pendiente(unit, make_charge, make_payment):
    cargo = make_charge(unit, date(2025, 1, 1))
    resultado = services.approve_payment(make_payment(unit, "50.00"))
    assert services.pending_amount(cargo) == D("30.00")
    assert resultado.charges_covered == 0
    assert resultado.surplus == D("0.00")
    assert services.credit_balance(unit) == D("0.00")


def test_dos_pagos_parciales_completan_el_cargo(unit, make_charge, make_payment):
    cargo = make_charge(unit, date(2025, 1, 1))
    services.approve_payment(make_payment(unit, "50.00"))
    segundo = services.approve_payment(make_payment(unit, "30.00"))
    assert services.pending_amount(cargo) == D("0.00")
    assert segundo.charges_covered == 1


def test_adelanto_con_excedente_queda_como_saldo_a_favor(
    unit, make_charge, make_payment
):
    cargo = make_charge(unit, date(2025, 1, 1))
    resultado = services.approve_payment(make_payment(unit, "200.00"))
    assert services.pending_amount(cargo) == D("0.00")
    assert resultado.surplus == D("120.00")
    assert services.credit_balance(unit) == D("120.00")


def test_pago_sin_cargos_abiertos_es_todo_saldo_a_favor(unit, make_payment):
    resultado = services.approve_payment(make_payment(unit, "80.00"))
    assert resultado.allocations == []
    assert resultado.surplus == D("80.00")
    assert services.credit_balance(unit) == D("80.00")


def test_un_pago_cubre_varios_cargos_en_orden(unit, make_charge, make_payment):
    c1 = make_charge(unit, date(2025, 1, 1))
    c2 = make_charge(unit, date(2025, 2, 1))
    c3 = make_charge(unit, date(2025, 3, 1))
    pago = make_payment(unit, "200.00")
    resultado = services.approve_payment(pago)
    assert asignado(pago, c1) == D("80.00")
    assert asignado(pago, c2) == D("80.00")
    assert asignado(pago, c3) == D("40.00")
    assert resultado.charges_covered == 2
    assert resultado.surplus == D("0.00")
    assert services.pending_amount(c3) == D("40.00")


def test_la_suma_de_asignaciones_nunca_supera_el_pago_ni_el_cargo(
    unit, make_charge, make_payment
):
    cargos = [make_charge(unit, date(2025, mes, 1)) for mes in (1, 2, 3)]
    pagos = [make_payment(unit, monto) for monto in ("70.00", "55.50", "300.00")]
    for pago in pagos:
        services.approve_payment(pago)
    for cargo in cargos:
        total = sum(
            a.amount for a in models.PaymentAllocation.objects.filter(charge=cargo)
        )
        assert total <= cargo.amount
    for pago in pagos:
        total = sum(a.amount for a in pago.allocations.all())
        assert total <= pago.amount


def test_se_paga_primero_el_cargo_mas_antiguo_sin_importar_el_orden_de_creacion(
    unit, make_charge, make_payment
):
    nuevo = make_charge(unit, date(2025, 3, 1))
    antiguo = make_charge(unit, date(2025, 1, 1))
    medio = make_charge(unit, date(2025, 2, 1))
    services.approve_payment(make_payment(unit, "80.00"))
    assert services.pending_amount(antiguo) == D("0.00")
    assert services.pending_amount(medio) == D("80.00")
    assert services.pending_amount(nuevo) == D("80.00")


def test_con_igual_vencimiento_gana_el_periodo_mas_antiguo(
    unit, make_charge, make_payment
):
    vence = date(2025, 4, 30)
    febrero = make_charge(unit, date(2025, 2, 1), due_date=vence)
    enero = make_charge(unit, date(2025, 1, 1), due_date=vence)
    services.approve_payment(make_payment(unit, "80.00"))
    assert services.pending_amount(enero) == D("0.00")
    assert services.pending_amount(febrero) == D("80.00")


@pytest.mark.parametrize("mas_antiguo", ["EXTRAORDINARY_FEE", "ADJUSTMENT"])
def test_con_igual_vencimiento_y_periodo_gana_el_creado_primero(
    unit, make_charge, make_payment, mas_antiguo
):
    vence = date(2025, 4, 30)
    cargos = {
        concepto: make_charge(unit, date(2025, 3, 1), concept=concepto, due_date=vence)
        for concepto in ("EXTRAORDINARY_FEE", "ADJUSTMENT")
    }
    # `created_at` lo pone auto_now_add y en Windows dos altas seguidas pueden
    # empatar (reloj de ~15 ms): se fija a mano para no depender del reloj.
    ahora = timezone.now()
    for concepto, cargo in cargos.items():
        creado = ahora - timedelta(seconds=1) if concepto == mas_antiguo else ahora
        models.Charge.objects.filter(pk=cargo.pk).update(created_at=creado)

    services.approve_payment(make_payment(unit, "80.00"))

    otro = "ADJUSTMENT" if mas_antiguo == "EXTRAORDINARY_FEE" else "EXTRAORDINARY_FEE"
    assert services.pending_amount(cargos[mas_antiguo]) == D("0.00")
    assert services.pending_amount(cargos[otro]) == D("80.00")


def test_ignora_cargos_anulados_y_refinanciados(unit, make_charge, make_payment):
    make_charge(unit, date(2024, 1, 1), status="VOID")
    make_charge(unit, date(2024, 2, 1), status="REFINANCED")
    abierto = make_charge(unit, date(2025, 1, 1))
    resultado = services.approve_payment(make_payment(unit, "80.00"))
    assert services.pending_amount(abierto) == D("0.00")
    assert resultado.surplus == D("0.00")
    assert models.PaymentAllocation.objects.count() == 1


def test_no_toca_los_cargos_de_otra_unidad(unit, unit2, make_charge, make_payment):
    ajeno = make_charge(unit2, date(2025, 1, 1))
    resultado = services.approve_payment(make_payment(unit, "80.00"))
    assert services.pending_amount(ajeno) == D("80.00")
    assert resultado.surplus == D("80.00")


def test_aprobar_dos_veces_no_duplica_asignaciones(unit, make_charge, make_payment):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, "80.00")
    services.approve_payment(pago)
    with pytest.raises(services.PaymentNotPendingError):
        services.approve_payment(pago)
    assert models.PaymentAllocation.objects.filter(payment=pago).count() == 1
    assert services.pending_amount(cargo) == D("0.00")
    assert AuditLog.objects.filter(action="PAYMENT_APPROVED").count() == 1


def test_dos_instancias_del_mismo_pago_tampoco_duplican(
    unit, make_charge, make_payment
):
    make_charge(unit, date(2025, 1, 1))
    original = make_payment(unit, "80.00")
    copia = models.Payment.objects.get(pk=original.pk)
    services.approve_payment(original)
    with pytest.raises(services.PaymentNotPendingError):
        services.approve_payment(copia)
    assert models.PaymentAllocation.objects.count() == 1


@pytest.mark.parametrize("estado", ["REJECTED", "VOID", "APPROVED"])
def test_solo_se_aprueba_un_pago_pendiente(unit, make_charge, make_payment, estado):
    make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, status=estado)
    with pytest.raises(services.PaymentNotPendingError):
        services.approve_payment(pago)
    assert models.PaymentAllocation.objects.count() == 0


def test_dos_pagos_seguidos_sobre_un_cargo_no_sobreasignan(
    unit, make_charge, make_payment
):
    cargo = make_charge(unit, date(2025, 1, 1))
    primero = make_payment(unit, "80.00")
    segundo = make_payment(unit, "80.00")
    services.approve_payment(primero)
    resultado = services.approve_payment(segundo)
    total = sum(a.amount for a in models.PaymentAllocation.objects.filter(charge=cargo))
    assert total == D("80.00")
    assert resultado.surplus == D("80.00")
    assert services.credit_balance(unit) == D("80.00")


def test_sin_actor_no_hay_revisor(unit, make_payment):
    pago = make_payment(unit, "80.00")
    services.approve_payment(pago)
    pago.refresh_from_db()
    assert pago.reviewed_by is None
    assert pago.reviewed_at is not None


# --- allocated_totals --------------------------------------------------------


def test_allocated_totals_sin_cargos_no_consulta_nada(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert services.allocated_totals([]) == {}


def test_allocated_totals_solo_cuenta_pagos_aprobados(unit, make_charge, make_payment):
    cargo = make_charge(unit, date(2025, 1, 1))
    for estado, monto in (
        ("PENDING", "10.00"),
        ("REJECTED", "20.00"),
        ("VOID", "30.00"),
        ("APPROVED", "40.00"),
    ):
        pago = make_payment(unit, "80.00", status=estado)
        models.PaymentAllocation.objects.create(
            payment=pago, charge=cargo, amount=D(monto)
        )
    assert services.allocated_totals([cargo.pk]) == {cargo.pk: D("40.00")}
    assert services.pending_amount(cargo) == D("40.00")


def test_allocated_totals_suma_varios_pagos_por_cargo(unit, make_charge, make_payment):
    cargo = make_charge(unit, date(2025, 1, 1))
    otro = make_charge(unit, date(2025, 2, 1))
    services.approve_payment(make_payment(unit, "30.00"))
    services.approve_payment(make_payment(unit, "70.00"))
    totales = services.allocated_totals([cargo.pk, otro.pk])
    assert totales == {cargo.pk: D("80.00"), otro.pk: D("20.00")}


# --- Efecto en el estado de cuenta y la morosidad ----------------------------


def test_el_estado_de_cuenta_refleja_lo_aplicado(unit, make_charge, make_payment):
    make_charge(unit, date(2025, 1, 1))
    make_charge(unit, date(2025, 2, 1))
    services.approve_payment(make_payment(unit, "100.00"))
    estado = services.unit_statement(unit, as_of=date(2025, 12, 31))
    primera, segunda = estado.lines
    assert (primera.allocated, primera.pending) == (D("80.00"), D("0.00"))
    assert (segunda.allocated, segunda.pending) == (D("20.00"), D("60.00"))
    assert estado.pending_balance == D("60.00")
    assert estado.months_of_debt == 1
    assert estado.credit_balance == D("0.00")


def test_el_estado_de_cuenta_informa_el_saldo_a_favor(unit, make_charge, make_payment):
    make_charge(unit, date(2025, 1, 1))
    services.approve_payment(make_payment(unit, "200.00"))
    assert services.unit_statement(unit).credit_balance == D("120.00")


def test_el_estado_de_cuenta_sin_pagos_tiene_saldo_a_favor_cero(unit):
    assert services.unit_statement(unit).credit_balance == D("0.00")


def test_pagar_saca_a_la_unidad_de_la_morosidad(unit, make_charge, make_payment):
    for mes in (1, 2, 3, 4):
        make_charge(unit, date(2025, mes, 1))
    fin = date(2025, 12, 31)
    assert services.is_delinquent(unit, fin)
    services.approve_payment(make_payment(unit, "80.00"))
    assert services.months_of_debt(unit, fin) == 3
    assert not services.is_delinquent(unit, fin)
    assert services.overdue_balance(unit, fin) == D("240.00")
    assert services.pending_balance(unit) == D("240.00")


# --- Auditoría ---------------------------------------------------------------


def test_audita_la_aprobacion_con_cargos_cubiertos_y_excedente(
    unit, admin, make_charge, make_payment
):
    make_charge(unit, date(2025, 1, 1))
    make_charge(unit, date(2025, 2, 1))
    pago = make_payment(unit, "200.00", operation_number="0012345678")
    services.approve_payment(pago, actor=admin, ip="10.0.0.5")
    evento = AuditLog.objects.get(action="PAYMENT_APPROVED")
    assert evento.entity_type == "Payment"
    assert evento.entity_id == str(pago.pk)
    assert evento.actor_id == str(admin.pk)
    assert evento.ip_address == "10.0.0.5"
    assert evento.changes["amount"] == "200.00"
    assert evento.changes["allocations"] == 2
    assert evento.changes["charges_covered"] == 2
    assert evento.changes["surplus"] == "40.00"
    assert "0012345678" not in json.dumps(evento.changes)
