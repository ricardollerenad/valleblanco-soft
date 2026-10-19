"""Saldo a favor: se deriva, se aplica solo a cargos nuevos y se recalcula."""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.models import AuditLog
from apps.finances import models, services

pytestmark = pytest.mark.django_db

D = Decimal
MARZO = date(2025, 3, 1)
ABRIL = date(2025, 4, 1)
MAYO = date(2025, 5, 1)


def pendiente_de(unit, periodo):
    cargo = models.Charge.objects.get(unit=unit, period=periodo)
    return services.pending_amount(cargo)


def consultas_de_pagos(contexto):
    return sum('"finances_payment"' in q["sql"] for q in contexto.captured_queries)


# --- Derivación --------------------------------------------------------------


def test_sin_pagos_el_saldo_a_favor_es_cero(unit):
    saldo = services.credit_balance(unit)
    assert saldo == D("0.00")
    assert isinstance(saldo, Decimal)


def test_el_saldo_es_lo_aprobado_menos_lo_asignado(unit, make_charge, pay):
    make_charge(unit, date(2025, 1, 1))
    pay(unit, "200.00")
    pay(unit, "30.50")
    assert services.credit_balance(unit) == D("150.50")


def test_solo_los_pagos_aprobados_dan_saldo(unit, make_payment):
    make_payment(unit, "10.00", status="PENDING")
    make_payment(unit, "20.00", status="REJECTED")
    make_payment(unit, "30.00", status="VOID")
    make_payment(unit, "40.00", status="APPROVED")
    assert services.credit_balance(unit) == D("40.00")


def test_el_saldo_es_por_unidad(unit, unit2, pay):
    pay(unit, "80.00")
    assert services.credit_balance(unit2) == D("0.00")


def test_el_saldo_nunca_es_negativo_ni_se_guarda(unit, make_charge, make_payment):
    cargo = make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, "50.00", status="APPROVED")
    models.PaymentAllocation.objects.create(
        payment=pago, charge=cargo, amount=D("80.00")
    )
    assert services.credit_balance(unit) == D("0.00")


# --- Aplicación a cargos nuevos ---------------------------------------------


def test_al_generar_cuotas_se_aplica_el_saldo_a_favor(unit, fee_rate, tower, pay):
    pay(unit, "200.00")
    services.generate_monthly_charges(MARZO, tower)
    assert pendiente_de(unit, MARZO) == D("0.00")
    assert services.credit_balance(unit) == D("120.00")
    services.generate_monthly_charges(ABRIL, tower)
    assert pendiente_de(unit, ABRIL) == D("0.00")
    assert services.credit_balance(unit) == D("40.00")
    services.generate_monthly_charges(MAYO, tower)
    assert pendiente_de(unit, MAYO) == D("40.00")
    assert services.credit_balance(unit) == D("0.00")


def test_generar_de_nuevo_no_vuelve_a_aplicar_saldo(unit, fee_rate, tower, pay):
    pay(unit, "200.00")
    services.generate_monthly_charges(MARZO, tower)
    services.generate_monthly_charges(MARZO, tower)
    assert services.credit_balance(unit) == D("120.00")


def test_se_usa_primero_el_pago_mas_antiguo_con_saldo(
    unit, fee_rate, tower, make_payment, admin
):
    reciente = make_payment(unit, "100.00", paid_at=date(2025, 2, 10))
    antiguo = make_payment(unit, "50.00", paid_at=date(2025, 1, 10))
    services.approve_payment(reciente, actor=admin)
    services.approve_payment(antiguo, actor=admin)
    services.generate_monthly_charges(MARZO, tower)
    cargo = models.Charge.objects.get(unit=unit, period=MARZO)
    asignaciones = {
        a.payment_id: a.amount
        for a in models.PaymentAllocation.objects.filter(charge=cargo)
    }
    assert asignaciones == {antiguo.pk: D("50.00"), reciente.pk: D("30.00")}
    assert services.credit_balance(unit) == D("70.00")


def test_solo_las_unidades_con_saldo_lo_reciben(unit, unit2, fee_rate, tower, pay):
    pay(unit, "80.00")
    services.generate_monthly_charges(MARZO, tower)
    assert pendiente_de(unit, MARZO) == D("0.00")
    assert pendiente_de(unit2, MARZO) == D("80.00")


def test_un_cargo_manual_recibe_el_saldo_a_favor(unit, pay):
    pay(unit, "80.00")
    cargo = services.create_manual_charge(unit, "EXTRAORDINARY_FEE", MARZO, "50.00")
    assert services.pending_amount(cargo) == D("0.00")
    assert services.credit_balance(unit) == D("30.00")


def test_un_cargo_manual_mayor_que_el_saldo_queda_con_pendiente(unit, pay):
    pay(unit, "30.00")
    cargo = services.create_manual_charge(unit, "ADJUSTMENT", MARZO, "50.00")
    assert services.pending_amount(cargo) == D("20.00")
    assert services.credit_balance(unit) == D("0.00")


def test_un_cargo_manual_sin_saldo_queda_intacto(unit):
    cargo = services.create_manual_charge(unit, "ADJUSTMENT", MARZO, "50.00")
    assert services.pending_amount(cargo) == D("50.00")
    assert models.PaymentAllocation.objects.count() == 0


def test_el_saldo_no_se_aplica_a_cargos_que_ya_existian(unit, make_charge, pay):
    antiguo = make_charge(unit, date(2025, 1, 1))
    primero = pay(unit, "80.00")
    pay(unit, "200.00")
    services.void_payment(primero, "Error de registro")
    # Conviven un cargo pendiente y saldo a favor de otro pago.
    assert services.pending_amount(antiguo) == D("80.00")
    assert services.credit_balance(unit) == D("200.00")

    nuevo = services.create_manual_charge(unit, "ADJUSTMENT", MARZO, "50.00")
    assert services.pending_amount(nuevo) == D("0.00")
    assert services.pending_amount(antiguo) == D("80.00")
    assert services.credit_balance(unit) == D("150.00")


def test_apply_credit_sin_ids_aplica_a_todos_los_pendientes_y_es_idempotente(
    unit, make_charge, pay
):
    antiguo = make_charge(unit, date(2025, 1, 1))
    primero = pay(unit, "80.00")
    pay(unit, "200.00")
    services.void_payment(primero, "Error de registro")
    aplicado = services.apply_credit(unit)
    assert aplicado == D("80.00")
    assert services.pending_amount(antiguo) == D("0.00")
    assert services.credit_balance(unit) == D("120.00")
    assert services.apply_credit(unit) == D("0.00")


def test_apply_credit_sin_saldo_devuelve_cero_y_no_audita(unit, make_charge):
    make_charge(unit, date(2025, 1, 1))
    assert services.apply_credit(unit) == D("0.00")
    assert not AuditLog.objects.filter(action="CREDIT_APPLIED").exists()


def test_apply_credit_con_ids_ajenos_o_cerrados_no_hace_nada(
    unit, unit2, make_charge, pay
):
    pay(unit, "80.00")
    ajeno = make_charge(unit2, date(2025, 1, 1))
    anulado = make_charge(unit, date(2025, 2, 1), status="VOID")
    assert services.apply_credit(unit, [ajeno.pk, anulado.pk]) == D("0.00")
    assert services.pending_amount(ajeno) == D("80.00")


# --- Anular recalcula --------------------------------------------------------


def test_anular_el_pago_que_dio_el_saldo_deja_pendiente_lo_aplicado(
    unit, fee_rate, tower, pay
):
    pago = pay(unit, "200.00")
    services.generate_monthly_charges(MARZO, tower)
    assert pendiente_de(unit, MARZO) == D("0.00")
    services.void_payment(pago, "Error de registro")
    assert pendiente_de(unit, MARZO) == D("80.00")
    assert services.credit_balance(unit) == D("0.00")
    assert models.PaymentAllocation.objects.filter(payment=pago).count() == 1


# --- Costo en consultas ------------------------------------------------------


def test_sin_saldo_a_favor_no_cuesta_consultas_por_unidad(tower, fee_rate, make_unit):
    for codigo in ("101", "102", "103"):
        make_unit(tower, codigo)
    with CaptureQueriesContext(connection) as pocas:
        services.generate_monthly_charges(MARZO, tower)
    for codigo in ("104", "105", "106", "107", "108", "201", "202", "203"):
        make_unit(tower, codigo)
    with CaptureQueriesContext(connection) as muchas:
        services.generate_monthly_charges(ABRIL, tower)
    assert consultas_de_pagos(pocas) == 1
    assert consultas_de_pagos(muchas) == 1


def test_un_cargo_manual_sin_saldo_cuesta_una_sola_consulta_de_pagos(unit):
    with CaptureQueriesContext(connection) as contexto:
        services.create_manual_charge(unit, "ADJUSTMENT", MARZO, "50.00")
    assert consultas_de_pagos(contexto) == 1


# --- Auditoría ---------------------------------------------------------------


def test_audita_la_aplicacion_del_saldo(unit, admin, fee_rate, tower, pay):
    pay(unit, "200.00", operation_number="0012345678")
    services.generate_monthly_charges(MARZO, tower, actor=admin, ip="10.0.0.5")
    evento = AuditLog.objects.get(action="CREDIT_APPLIED")
    assert evento.entity_type == "Unit"
    assert evento.entity_id == str(unit.pk)
    assert evento.actor_id == str(admin.pk)
    assert evento.ip_address == "10.0.0.5"
    assert evento.changes["applied"] == "80.00"
    assert evento.changes["charges"] == 1
    assert evento.changes["payments"] == 1
    assert "0012345678" not in json.dumps(evento.changes)


def test_la_generacion_automatica_audita_con_la_etiqueta_del_actor(
    unit, fee_rate, tower, pay
):
    pay(unit, "80.00")
    services.generate_monthly_charges(MARZO, tower, actor_label="cron")
    evento = AuditLog.objects.get(action="CREDIT_APPLIED")
    assert evento.actor_label == "cron"
