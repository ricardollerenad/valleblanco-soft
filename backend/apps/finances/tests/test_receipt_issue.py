"""S5-A: emisión del recibo al aprobar un pago (servicio `approve_payment`)."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.db import transaction
from django.utils import timezone

from apps.core.models import AuditLog
from apps.finances import services

pytestmark = pytest.mark.django_db

OPERACION = "BCP98765432"


def anio():
    return timezone.localdate().year


def numero(n, year=None):
    return f"R-{year or anio()}-{n:06d}"


def recibo(pago):
    from apps.finances.models import Receipt

    return Receipt.objects.get(payment=pago)


def total_recibos():
    from apps.finances.models import Receipt

    return Receipt.objects.count()


# --- Emisión y correlativo ---------------------------------------------------


def test_aprobar_un_pago_emite_su_recibo(unit, make_charge, make_payment, admin):
    make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, "80.00")

    services.approve_payment(pago, actor=admin)

    emitido = recibo(pago)
    assert emitido.number == numero(1)
    assert emitido.payment_id == pago.pk
    assert abs(timezone.now() - emitido.issued_at).total_seconds() < 60


def test_el_correlativo_sube_de_uno_en_uno(unit, unit2, make_payment, admin):
    pagos = [make_payment(u) for u in (unit, unit2, unit)]

    for pago in pagos:
        services.approve_payment(pago, actor=admin)

    assert [recibo(p).number for p in pagos] == [numero(1), numero(2), numero(3)]


def test_el_correlativo_reinicia_cada_anio(unit, make_payment, admin, monkeypatch):
    from apps.finances import receipts

    pagos = [make_payment(unit) for _ in range(3)]
    # 01-ene-2027 03:00 UTC sigue siendo 31-dic-2026 en Lima (UTC-5).
    momentos = iter(
        [
            datetime(2027, 1, 1, 3, 0, tzinfo=UTC),
            datetime(2027, 1, 1, 12, 0, tzinfo=UTC),
            datetime(2027, 1, 1, 13, 0, tzinfo=UTC),
        ]
    )
    monkeypatch.setattr(receipts, "_ahora", lambda: next(momentos))

    for pago in pagos:
        services.approve_payment(pago, actor=admin)

    assert [recibo(p).number for p in pagos] == [
        "R-2026-000001",
        "R-2027-000001",
        "R-2027-000002",
    ]


def test_el_numero_tiene_el_formato_pedido(unit, make_payment, admin):
    pago = make_payment(unit)

    services.approve_payment(pago, actor=admin)

    assert recibo(pago).number == f"R-{anio()}-000001"


def test_rechazar_o_dejar_pendiente_no_emite_recibo(unit, make_payment, admin):
    pendiente = make_payment(unit)
    rechazado = make_payment(unit)

    services.reject_payment(rechazado, "Foto ilegible", actor=admin)

    assert total_recibos() == 0
    assert pendiente.status == "PENDING"


def test_un_pago_legacy_no_emite_recibo_ni_gasta_numero(unit, make_payment, admin):
    historico = make_payment(unit, method="LEGACY", operation_number="")
    normal = make_payment(unit)

    services.approve_payment(historico, actor=admin)
    services.approve_payment(normal, actor=admin)

    assert total_recibos() == 1
    assert recibo(normal).number == numero(1)


def test_si_la_aprobacion_se_revierte_no_quedan_huecos(unit, make_payment, admin):
    primero = make_payment(unit)
    segundo = make_payment(unit)

    with pytest.raises(RuntimeError), transaction.atomic():
        services.approve_payment(primero, actor=admin)
        raise RuntimeError("falla posterior que revierte todo")
    services.approve_payment(segundo, actor=admin)

    assert total_recibos() == 1
    assert recibo(segundo).number == numero(1)


def test_si_falla_la_emision_no_se_aprueba_el_pago(
    unit, make_charge, make_payment, admin, monkeypatch
):
    """El recibo se emite en la misma transacción que la aplicación a cargos."""
    from apps.finances import receipts
    from apps.finances.models import Payment, PaymentAllocation

    make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit)

    def falla(*args, **kwargs):
        raise RuntimeError("no se pudo emitir")

    monkeypatch.setattr(receipts, "issue_receipt", falla)

    with pytest.raises(RuntimeError):
        services.approve_payment(pago, actor=admin)

    assert Payment.objects.get(pk=pago.pk).status == "PENDING"
    assert PaymentAllocation.objects.count() == 0
    assert not AuditLog.objects.filter(action="PAYMENT_APPROVED").exists()


def test_aplicar_saldo_a_favor_no_emite_otro_recibo(
    unit, make_charge, make_payment, admin
):
    make_charge(unit, date(2025, 1, 1))
    services.approve_payment(make_payment(unit, "100.00"), actor=admin)
    futuro = make_charge(unit, date(2025, 2, 1))

    services.apply_credit(unit, [futuro.pk], actor=admin)

    assert total_recibos() == 1


# --- Contenido del snapshot --------------------------------------------------


@pytest.fixture
def pago_con_titular(unit, make_charge, make_payment, make_resident, admin):
    """Pago de 100.00 con cargos de ene y feb: cubre enero y 20.00 de febrero."""
    make_resident("titular@valleblanco.pe", unit, "OWNER", True)
    make_charge(unit, date(2025, 1, 1))
    make_charge(unit, date(2025, 2, 1))
    pago = make_payment(
        unit,
        "100.00",
        method="TRANSFER",
        operation_number=OPERACION,
        paid_at=date(2025, 3, 1),
    )
    services.approve_payment(pago, actor=admin)
    return pago


def test_el_snapshot_trae_los_datos_del_recibo(pago_con_titular):
    emitido = recibo(pago_con_titular)

    s = emitido.snapshot

    assert s["version"] == 1
    assert s["number"] == emitido.number
    assert s["issued_at"] == emitido.issued_at.isoformat()
    assert s["condominium"] == "ValleBlanco"
    assert s["tower"] == "Torre 2"
    assert s["unit"] == "101"
    assert s["holder"] == {"name": "titular"}
    assert s["amount"] == "100.00"
    assert s["method"] == "TRANSFER"
    assert s["method_label"] == "Transferencia"
    assert s["paid_at"] == "2025-03-01"
    assert s["credit_remainder"] == "0.00"


def test_el_snapshot_lista_los_periodos_cubiertos(pago_con_titular):
    periodos = recibo(pago_con_titular).snapshot["periods"]

    assert [(p["period"], p["applied"], p["covered"]) for p in periodos] == [
        ("2025-01-01", "80.00", True),
        ("2025-02-01", "20.00", False),
    ]
    assert periodos[0]["concept"] == "ORDINARY_FEE"
    assert periodos[0]["concept_label"] == "Cuota ordinaria"


def test_el_excedente_queda_como_saldo_a_favor_en_el_snapshot(
    unit, make_charge, make_payment, admin
):
    make_charge(unit, date(2025, 1, 1))
    pago = make_payment(unit, "100.00")

    services.approve_payment(pago, actor=admin)

    s = recibo(pago).snapshot
    assert s["credit_remainder"] == "20.00"
    assert [p["period"] for p in s["periods"]] == ["2025-01-01"]


def test_un_pago_sin_cargos_abiertos_emite_recibo_sin_periodos(
    unit, make_payment, admin
):
    pago = make_payment(unit, "80.00")

    services.approve_payment(pago, actor=admin)

    s = recibo(pago).snapshot
    assert s["periods"] == []
    assert s["credit_remainder"] == "80.00"


def test_la_operacion_se_guarda_enmascarada(pago_con_titular):
    emitido = recibo(pago_con_titular)

    assert emitido.snapshot["operation"] == "****5432"
    assert OPERACION not in json.dumps(emitido.snapshot)


def test_una_operacion_corta_tambien_se_enmascara(unit, make_payment, admin):
    pago = make_payment(unit, operation_number="AB1")

    services.approve_payment(pago, actor=admin)

    assert recibo(pago).snapshot["operation"] == "****AB1"


def test_sin_titular_principal_el_recibo_se_emite_igual(unit, make_payment, admin):
    pago = make_payment(unit)

    services.approve_payment(pago, actor=admin)

    assert recibo(pago).snapshot["holder"] is None


def test_el_titular_es_el_principal_vigente_no_un_copropietario(
    unit, make_payment, make_resident, admin
):
    make_resident(
        "antiguo@valleblanco.pe", unit, "OWNER", True, end_date=date(2024, 6, 1)
    )
    make_resident("copropietario@valleblanco.pe", unit, "OWNER", False)
    make_resident("inquilino2@valleblanco.pe", unit, "TENANT", False)
    make_resident("actual@valleblanco.pe", unit, "OWNER", True)
    pago = make_payment(unit)

    services.approve_payment(pago, actor=admin)

    assert recibo(pago).snapshot["holder"] == {"name": "actual"}


def test_el_snapshot_no_cambia_si_cambian_los_datos_despues(pago_con_titular, unit):
    from apps.properties.models import Person

    antes = recibo(pago_con_titular).snapshot
    Person.objects.filter(full_name="titular").update(full_name="Otra Persona")
    unit.tower.name = "Torre 9"
    unit.tower.save()

    despues = recibo(pago_con_titular).snapshot

    assert despues == antes
    assert despues["holder"] == {"name": "titular"}
    assert despues["tower"] == "Torre 2"


def test_anular_el_pago_conserva_el_recibo_intacto(pago_con_titular, admin):
    antes = recibo(pago_con_titular)

    services.void_payment(pago_con_titular, "Pago duplicado", actor=admin)

    despues = recibo(pago_con_titular)
    assert despues.pk == antes.pk
    assert despues.number == antes.number
    assert despues.snapshot == antes.snapshot
    assert total_recibos() == 1


# --- Auditoría ---------------------------------------------------------------


def test_se_audita_la_emision_con_actor_e_ip(unit, make_payment, admin):
    pago = make_payment(unit, "80.00", operation_number=OPERACION)

    services.approve_payment(pago, actor=admin, ip="203.0.113.7")

    evento = AuditLog.objects.get(action="RECEIPT_ISSUED")
    assert evento.entity_type == "Receipt"
    assert evento.entity_id == str(recibo(pago).pk)
    assert evento.actor_id == str(admin.pk)
    assert evento.ip_address == "203.0.113.7"
    assert evento.changes == {
        "number": numero(1),
        "payment": str(pago.pk),
        "unit": "101",
        "amount": "80.00",
    }


def test_la_auditoria_nunca_guarda_el_numero_de_operacion(unit, make_payment, admin):
    pago = make_payment(unit, operation_number=OPERACION)

    services.approve_payment(pago, actor=admin)

    for evento in AuditLog.objects.all():
        assert OPERACION not in json.dumps(evento.changes)


def test_solo_un_evento_de_emision_por_pago(unit, make_payment, admin):
    pago = make_payment(unit)

    services.approve_payment(pago, actor=admin)
    with pytest.raises(services.PaymentNotPendingError):
        services.approve_payment(pago, actor=admin)

    assert AuditLog.objects.filter(action="RECEIPT_ISSUED").count() == 1


def test_el_monto_del_snapshot_es_decimal_con_dos_decimales(unit, make_payment, admin):
    pago = make_payment(unit, "80.5")

    services.approve_payment(pago, actor=admin)

    assert recibo(pago).snapshot["amount"] == "80.50"
    assert Decimal(recibo(pago).snapshot["amount"]) == Decimal("80.50")
