"""Condominio, torre, unidades, personas, ocupaciones y roles por torre."""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Condominium(BaseModel):
    name = models.CharField("nombre", max_length=150, unique=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "condominio"
        verbose_name_plural = "condominios"

    def __str__(self):
        return self.name


class Tower(BaseModel):
    condominium = models.ForeignKey(
        Condominium, on_delete=models.PROTECT, related_name="towers"
    )
    name = models.CharField("nombre", max_length=100)
    floors = models.PositiveSmallIntegerField(
        "pisos", validators=[MinValueValidator(1)]
    )

    class Meta:
        ordering = ("condominium__name", "name")
        verbose_name = "torre"
        verbose_name_plural = "torres"
        constraints = (
            models.UniqueConstraint(
                fields=("condominium", "name"), name="uniq_tower_name_per_condominium"
            ),
        )

    def __str__(self):
        return self.name


class Unit(BaseModel):
    tower = models.ForeignKey(Tower, on_delete=models.PROTECT, related_name="units")
    code = models.CharField("código", max_length=10)
    floor = models.PositiveSmallIntegerField("piso")
    number = models.PositiveSmallIntegerField("número en el piso")
    billing_start = models.DateField("inicio de cobro", null=True, blank=True)
    is_active = models.BooleanField("activa", default=True)

    class Meta:
        ordering = ("tower__name", "floor", "number")
        verbose_name = "unidad"
        verbose_name_plural = "unidades"
        constraints = (
            models.UniqueConstraint(
                fields=("tower", "code"), name="uniq_unit_code_per_tower"
            ),
        )

    def __str__(self):
        return f"{self.tower.name} - {self.code}"


class Person(BaseModel):
    """Existe aunque no tenga usuario. `user` la enlaza con su cuenta de acceso."""

    class DocType(models.TextChoices):
        DNI = "DNI", "DNI"
        CE = "CE", "Carné de extranjería"
        PASSPORT = "PASSPORT", "Pasaporte"
        OTHER = "OTHER", "Otro"

    full_name = models.CharField("nombre completo", max_length=255)
    first_names = models.CharField("nombres", max_length=150, blank=True)
    last_names = models.CharField("apellidos", max_length=150, blank=True)
    doc_type = models.CharField(
        "tipo de documento", max_length=10, choices=DocType.choices, blank=True
    )
    doc_number = models.CharField("número de documento", max_length=20, blank=True)
    email = models.EmailField("correo", blank=True)
    phone = models.CharField("teléfono", max_length=30, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="person",
    )

    class Meta:
        ordering = ("full_name",)
        verbose_name = "persona"
        verbose_name_plural = "personas"
        constraints = (
            models.UniqueConstraint(
                fields=("doc_type", "doc_number"),
                condition=~Q(doc_number=""),
                name="uniq_person_document",
            ),
        )

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        if not self.full_name:
            self.full_name = f"{self.first_names} {self.last_names}".strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name


class UnitOccupancy(BaseModel):
    """Propietario o inquilino de una unidad. Vigente mientras `end_date` sea nulo."""

    class Relation(models.TextChoices):
        OWNER = "OWNER", "Propietario"
        TENANT = "TENANT", "Inquilino"

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="occupancies")
    person = models.ForeignKey(
        Person, on_delete=models.PROTECT, related_name="occupancies"
    )
    relation = models.CharField("relación", max_length=10, choices=Relation.choices)
    is_primary = models.BooleanField("titular principal", default=False)
    start_date = models.DateField("desde")
    end_date = models.DateField("hasta", null=True, blank=True)

    class Meta:
        ordering = ("unit__floor", "unit__number", "-start_date")
        verbose_name = "ocupación"
        verbose_name_plural = "ocupaciones"
        constraints = (
            models.UniqueConstraint(
                fields=("unit",),
                condition=Q(is_primary=True, end_date__isnull=True),
                name="uniq_primary_holder_per_unit",
            ),
            models.CheckConstraint(
                condition=Q(is_primary=False) | Q(relation="OWNER"),
                name="primary_holder_must_be_owner",
            ),
            models.CheckConstraint(
                condition=Q(end_date__isnull=True)
                | Q(end_date__gte=models.F("start_date")),
                name="occupancy_end_not_before_start",
            ),
            models.UniqueConstraint(
                fields=("unit", "person", "relation"),
                condition=Q(end_date__isnull=True),
                name="uniq_active_occupancy_per_person_relation",
            ),
        )

    @property
    def is_active(self):
        return self.end_date is None

    def __str__(self):
        return f"{self.person} - {self.unit} ({self.relation})"


class RoleAssignment(BaseModel):
    """Rol de gestión por torre. El tesorero es un cargo de la junta."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrador"
        BOARD = "BOARD", "Junta directiva"
        TREASURER = "TREASURER", "Tesorero"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="role_assignments",
    )
    role = models.CharField("rol", max_length=10, choices=Role.choices)
    tower = models.ForeignKey(
        Tower, on_delete=models.CASCADE, related_name="role_assignments"
    )

    class Meta:
        ordering = ("tower__name", "role")
        verbose_name = "asignación de rol"
        verbose_name_plural = "asignaciones de rol"
        constraints = (
            models.UniqueConstraint(
                fields=("user", "role", "tower"), name="uniq_role_per_user_and_tower"
            ),
        )

    def __str__(self):
        return f"{self.user} - {self.role} ({self.tower})"
