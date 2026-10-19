"""T6: helper que escribe en AuditLog."""

import pytest
from django.contrib.auth import get_user_model

from apps.core.audit import log_event
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_registra_el_actor_como_texto():
    user = User.objects.create_user("ana@valleblanco.pe", "Cielo-Azul-9347-Zq")

    entry = log_event("DEMO_ACTION", "Demo", 123, actor=user, ip="10.0.0.5")

    assert entry.actor_id == str(user.pk)
    assert entry.actor_label == "ana@valleblanco.pe"
    assert entry.action == "DEMO_ACTION"
    assert entry.entity_type == "Demo"
    assert entry.entity_id == "123"
    assert entry.ip_address == "10.0.0.5"
    assert entry.changes == {}


def test_sin_actor_usa_la_etiqueta_indicada():
    entry = log_event("DEMO_ACTION", "Demo", "", actor_label="intento@x.pe")

    assert entry.actor_id == ""
    assert entry.actor_label == "intento@x.pe"
    assert entry.ip_address is None


def test_ip_vacia_se_guarda_como_nula():
    entry = log_event("DEMO_ACTION", "Demo", "1", ip="")
    assert entry.ip_address is None


def test_guarda_los_cambios_y_persiste():
    log_event("DEMO_ACTION", "Demo", "1", changes={"campo": "valor"})
    assert AuditLog.objects.get().changes == {"campo": "valor"}
