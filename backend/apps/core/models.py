# backend/apps/core/models.py
"""Modelos base del proyecto."""

import uuid

from django.db import models


class AuditLogImmutableError(Exception):
    """Se intentó modificar o borrar un registro de auditoría (es de solo escritura)."""


class BaseModel(models.Model):
    """Base abstracta: clave UUID v4 y marcas de creación y actualización."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLogQuerySet(models.QuerySet):
    """Bloquea las modificaciones y borrados masivos."""

    def update(self, **kwargs):
        raise AuditLogImmutableError(
            "Los registros de auditoría no se pueden modificar."
        )

    def delete(self):
        raise AuditLogImmutableError("Los registros de auditoría no se pueden borrar.")


class AuditLog(BaseModel):
    """Quién hizo qué, sobre qué entidad, con qué cambios, desde qué IP y cuándo."""

    actor_id = models.CharField(max_length=64, blank=True)
    actor_label = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=64)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditLogImmutableError(
                "Los registros de auditoría no se pueden modificar."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogImmutableError("Los registros de auditoría no se pueden borrar.")
