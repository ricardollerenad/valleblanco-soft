"""Escritura en AuditLog. El actor se guarda como texto, sin clave foránea."""

from .models import AuditLog


def log_event(
    action,
    entity_type,
    entity_id,
    *,
    actor=None,
    actor_label="",
    changes=None,
    ip=None,
):
    """Registra un evento sensible. Nunca debe recibir contraseñas ni tokens."""
    actor_id = ""
    if actor is not None and getattr(actor, "pk", None) is not None:
        actor_id = str(actor.pk)
        actor_label = getattr(actor, "email", "") or str(actor)
    return AuditLog.objects.create(
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        changes=changes or {},
        ip_address=ip or None,
    )
