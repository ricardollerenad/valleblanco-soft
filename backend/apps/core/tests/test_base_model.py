"""RED: BaseModel (UUID v4 + marcas de tiempo) todavía no existe."""

import uuid

from django.db import models

from apps.core.models import BaseModel


def test_base_model_is_abstract():
    assert BaseModel._meta.abstract is True


def test_base_model_uses_uuid4_primary_key():
    field = BaseModel._meta.get_field("id")

    assert isinstance(field, models.UUIDField)
    assert field.primary_key is True
    assert field.default is uuid.uuid4
    assert field.editable is False


def test_base_model_has_creation_and_update_timestamps():
    created = BaseModel._meta.get_field("created_at")
    updated = BaseModel._meta.get_field("updated_at")

    assert isinstance(created, models.DateTimeField)
    assert created.auto_now_add is True
    assert isinstance(updated, models.DateTimeField)
    assert updated.auto_now is True
