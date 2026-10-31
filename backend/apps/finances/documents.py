"""HTML y PDF de los documentos de finanzas: recibo y estado de cuenta.

Las plantillas reciben textos ya formateados (fechas `dd/mm/aaaa`, montos `S/ 0.00`),
así no hay lógica de presentación en el HTML. El recibo se arma SIEMPRE desde el
`snapshot` guardado, nunca desde los datos actuales. El PDF se pide al módulo
`pdf` (`pdf.render_pdf`), que las pruebas pueden sustituir.
"""

from datetime import date, datetime
from decimal import Decimal

from django.template.loader import render_to_string
from django.utils import timezone

from . import pdf
from .models import Concept, Payment

MESES = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)


def fecha(value):
    """`2025-03-01` o `date` -> `01/03/2025`."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return f"{value:%d/%m/%Y}"


def periodo(value):
    """`2025-01-01` o `date` -> `Enero 2025`."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return f"{MESES[value.month - 1]} {value.year}"


def soles(value):
    return f"S/ {value:,.2f}"


def soles_texto(texto):
    """`"80.00"` (monto guardado en el snapshot) -> `S/ 80.00`."""
    return soles(Decimal(texto))


def _ahora_local():
    return f"{timezone.localtime():%d/%m/%Y %H:%M}"


def receipt_html(receipt, *, voided=False):
    """HTML del recibo, construido solo con los datos del snapshot."""
    s = receipt.snapshot
    emitido = timezone.localtime(datetime.fromisoformat(s["issued_at"]))
    credito = s["credit_remainder"]
    contexto = {
        "numero": s["number"],
        "condominio": s["condominium"],
        "torre": s["tower"],
        "unidad": s["unit"],
        "titular": (s["holder"] or {}).get("name", ""),
        "fecha_pago": fecha(s["paid_at"]),
        "medio": s["method_label"],
        "operacion": s["operation"],
        "emitido": f"{emitido:%d/%m/%Y %H:%M}",
        "monto": soles_texto(s["amount"]),
        "saldo_a_favor": soles_texto(credito) if Decimal(credito) > 0 else "",
        "anulado": voided,
        "periodos": [
            {
                "periodo": periodo(p["period"]),
                "concepto": p["concept_label"],
                "descripcion": p.get("description", ""),
                "aplicado": soles_texto(p["applied"]),
                "cubierto": p["covered"],
            }
            for p in s["periods"]
        ],
    }
    return render_to_string("finances/pdf/recibo.html", contexto)


def receipt_pdf(receipt, payment_status):
    """PDF del recibo; si el pago está anulado, el PDF lleva el sello «ANULADO»."""
    html = receipt_html(receipt, voided=payment_status == Payment.Status.VOID)
    return pdf.render_pdf(html)


def statement_html(statement):
    """HTML del estado de cuenta a partir del resultado de `services.unit_statement`."""
    unidad = statement.unit
    lineas = [
        {
            "periodo": periodo(linea.charge.period),
            "concepto": Concept(linea.charge.concept).label,
            "descripcion": linea.charge.description,
            "vence": fecha(linea.charge.due_date),
            "monto": soles(linea.charge.amount),
            "pagado": soles(linea.allocated),
            "pendiente": soles(linea.pending),
            "vencido": linea.is_overdue,
            "saldado": linea.pending <= 0,
        }
        for linea in statement.lines
    ]
    contexto = {
        "condominio": unidad.tower.condominium.name,
        "torre": unidad.tower.name,
        "unidad": unidad.code,
        "al_dia_de": fecha(statement.as_of),
        "saldo_pendiente": soles(statement.pending_balance),
        "saldo_vencido": soles(statement.overdue_balance),
        "meses_deuda": statement.months_of_debt,
        "umbral": statement.threshold,
        "moroso": statement.is_delinquent,
        "saldo_a_favor": soles(statement.credit_balance),
        "lineas": lineas,
        "emitido": _ahora_local(),
    }
    return render_to_string("finances/pdf/estado_cuenta.html", contexto)


def statement_pdf(statement):
    return pdf.render_pdf(statement_html(statement))
