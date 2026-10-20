# NOTA DE TRASPASO S4-A -> S4-B (finances II: pagos, sin API)
Estado: 735 pruebas en PostgreSQL 16 (565 previas intactas + 170 nuevas); 732 + 3 saltadas en SQLite. Cobertura finances 98 %.

## Modelos (models.py, migración 0002_payments)
- Payment: unit, amount, method (TRANSFER/DEPOSIT/YAPE/PLIN/LEGACY), operation_number (se guarda NORMALIZADO), paid_at, status (PENDING/APPROVED/REJECTED/VOID), submitted_by, reviewed_by, reviewed_at, rejection_reason, source (RESIDENT/STAFF/IMPORT). Relaciones: unit.payments, payment.vouchers, payment.allocations, charge.allocations.
- PaymentVoucher: payment, file (storage privado; `.url` lanza ValueError), sha256, content_type, size.
- PaymentAllocation: payment, charge, amount; UNIQUE(payment, charge). Solo cuentan las de pagos APPROVED.

## Servicios (services.py; todos con actor=, ip=)
- submit_payment(unit, amount, method, operation_number, paid_at, *, source=RESIDENT, submitted_by=None) -> Payment
- attach_voucher(payment, content) -> VoucherAttachment(voucher, duplicate_payment_ids); content = bytes o UploadedFile
- approve_payment(payment) -> PaymentApproval(payment, allocations, charges_covered, surplus)
- reject_payment(payment, reason) / void_payment(payment, reason) -> Payment (el objeto recibido queda actualizado)
- credit_balance(unit) -> Decimal; units_with_credit(ids) -> set; apply_credit(unit, charge_ids=None, actor_label="") -> Decimal aplicado
- unit_statement(...).credit_balance: campo nuevo; el serializer aún NO lo expone. allocated_totals ya suma solo APPROVED.
- to_money(value, error=InvalidChargeError): acepta la clase de error.

## Errores (subclases de FinancesError)
InvalidPaymentError, DuplicatePaymentError (.existing_payment_id), PaymentNotPendingError, PaymentNotApprovedError, InvalidVoucherError, VoucherLimitError. Sugerido: 400 los Invalid*; 409 el resto.

## Auditoría
PAYMENT_SUBMITTED, VOUCHER_ATTACHED, PAYMENT_APPROVED (allocations, charges_covered, surplus), PAYMENT_REJECTED, PAYMENT_VOIDED, CREDIT_APPLIED (entidad Unit). Número de operación: solo los últimos 4.

## Fixtures nuevos (tests/conftest.py)
voucher_root (autouse, directorio temporal), make_payment(unit, amount, **extra), pay(unit, amount, approve=True, **extra), voucher_file(kind, salt=None, size=None).

## Supuestos
1. operation_number: solo alfanuméricos en mayúsculas, máx. 50; es lo que se guarda y se compara. 2. paid_at no futura; LEGACY solo con source=IMPORT.
3. Los servicios NO autorizan: S4-B debe filtrar por unidad (ajena -> 404) y rol; submit no verifica que el actor viva en la unidad.
4. Bloqueos: unidad -> pago -> cargos; submit_payment bloquea la torre. 5. El saldo a favor solo se aplica a cargos nuevos: tras anular un pago pueden coexistir deuda y saldo; apply_credit(unit) lo aplica a mano (S4-B puede exponerlo solo para ADMIN).
6. Mismo sha256 en el mismo pago -> InvalidVoucherError; en otro pago de la torre solo avisa (S4-B no debe revelar a un residente pagos de otras unidades).
7. El motivo (<=255) va tal cual a la auditoría. 8. Nada impide que quien registró un pago lo apruebe: es decisión de permisos de S4-B.

## Límites / pendiente
- Máx. 3 comprobantes y "suma de asignaciones <= monto" se garantizan en servicio con select_for_update, no con restricción de BD.
- Descarga (S4-B): FileResponse tras permisos, Content-Type desde la BD, nosniff y attachment; nunca voucher.file.url. Sin antivirus.
- Subidas: Nginx client_max_body_size >= 6 MB (Fase 5). Docker NO verificado: Dockerfile fija VOUCHER_ROOT=/data/vouchers; compose debe montar un volumen nombrado ahí; añadir backend/var/ a .gitignore.
- Las 3 pruebas con hilos solo corren en PostgreSQL. ARCHITECTURE y ROADMAP sin cambios (documentar tras S4-B).
