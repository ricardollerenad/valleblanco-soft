# backend/apps/core/tests/test_audit_log.py
"""RED: AuditLog (registro de auditoría de solo escritura) todavía no existe."""

import uuid

import pytest
from django.utils import timezone

from apps.core.models import AuditLog, AuditLogImmutableError

pytestmark = pytest.mark.django_db


def make_entry(**overrides):
    data = {
        "actor_id": "11111111-1111-4111-8111-111111111111",
        "actor_label": "admin@example.com",
        "action": "PAYMENT_APPROVED",
        "entity_type": "Payment",
        "entity_id": "22222222-2222-4222-8222-222222222222",
        "changes": {"status": ["PENDING", "APPROVED"]},
        "ip_address": "127.0.0.1",
    }
    data.update(overrides)
    return AuditLog.objects.create(**data)


def test_entry_can_be_created_and_read_back():
    entry = make_entry()
    entry.refresh_from_db()

    assert isinstance(entry.id, uuid.UUID)
    assert entry.id.version == 4
    assert timezone.is_aware(entry.created_at)
    assert entry.action == "PAYMENT_APPROVED"
    assert entry.entity_type == "Payment"
    assert entry.changes == {"status": ["PENDING", "APPROVED"]}
    assert entry.ip_address == "127.0.0.1"


def test_actor_and_ip_are_optional_for_system_actions():
    entry = make_entry(actor_id="", actor_label="", ip_address=None)

    assert AuditLog.objects.filter(pk=entry.pk).exists()


def test_existing_entry_cannot_be_modified():
    entry = make_entry()
    entry.action = "TAMPERED"

    with pytest.raises(AuditLogImmutableError):
        entry.save()

    entry.refresh_from_db()
    assert entry.action == "PAYMENT_APPROVED"


def test_entry_cannot_be_deleted():
    entry = make_entry()

    with pytest.raises(AuditLogImmutableError):
        entry.delete()

    assert AuditLog.objects.count() == 1


def test_queryset_update_is_blocked():
    make_entry()

    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.update(action="TAMPERED")

    assert AuditLog.objects.filter(action="TAMPERED").count() == 0


def test_queryset_delete_is_blocked():
    make_entry()

    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.all().delete()

    assert AuditLog.objects.count() == 1
