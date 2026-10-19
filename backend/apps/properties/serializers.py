from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from .models import Person, RoleAssignment, Tower, Unit, UnitOccupancy


class UnitSerializer(serializers.ModelSerializer):
    tower_name = serializers.CharField(source="tower.name", read_only=True)

    class Meta:
        model = Unit
        fields = (
            "id",
            "tower",
            "tower_name",
            "code",
            "floor",
            "number",
            "billing_start",
            "is_active",
        )
        read_only_fields = ("id", "tower", "tower_name", "code", "floor", "number")


class PersonSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    has_user = serializers.SerializerMethodField()

    class Meta:
        model = Person
        fields = (
            "id",
            "full_name",
            "first_names",
            "last_names",
            "doc_type",
            "doc_number",
            "email",
            "phone",
            "has_user",
        )
        read_only_fields = ("id", "has_user")
        validators = ()  # la unicidad del documento se valida en validate()

    def get_has_user(self, obj):
        return obj.user_id is not None

    def validate(self, attrs):
        current = self.instance

        def get(name):
            return attrs.get(name, getattr(current, name, "") if current else "")

        if not (get("full_name") or get("first_names") or get("last_names")):
            raise serializers.ValidationError(
                {"full_name": "Indica el nombre completo o nombres y apellidos."}
            )
        doc_type, doc_number = get("doc_type"), get("doc_number")
        if doc_number and not doc_type:
            raise serializers.ValidationError(
                {"doc_type": "Indica el tipo de documento."}
            )
        if doc_number:
            duplicates = Person.objects.filter(doc_type=doc_type, doc_number=doc_number)
            if current:
                duplicates = duplicates.exclude(pk=current.pk)
            if duplicates.exists():
                raise serializers.ValidationError(
                    {"doc_number": "Ya existe una persona con ese documento."}
                )
        return attrs


class PersonInviteSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)


class OccupancySerializer(serializers.ModelSerializer):
    person_name = serializers.CharField(source="person.full_name", read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = UnitOccupancy
        fields = (
            "id",
            "unit",
            "unit_code",
            "person",
            "person_name",
            "relation",
            "is_primary",
            "start_date",
            "end_date",
            "is_active",
        )
        read_only_fields = fields


class OccupancyCreateSerializer(serializers.Serializer):
    person = serializers.PrimaryKeyRelatedField(queryset=Person.objects.all())
    relation = serializers.ChoiceField(choices=UnitOccupancy.Relation.choices)
    is_primary = serializers.BooleanField(required=False, default=False)
    start_date = serializers.DateField(required=False, default=timezone.localdate)
    end_date = serializers.DateField(required=False, allow_null=True, default=None)


class OccupancyUpdateSerializer(serializers.Serializer):
    is_primary = serializers.BooleanField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Indica is_primary o end_date.")
        if "is_primary" in attrs and "end_date" in attrs:
            raise serializers.ValidationError(
                "Cambia el titular o cierra la ocupación, no ambas a la vez."
            )
        if attrs.get("is_primary") is False:
            raise serializers.ValidationError(
                {"is_primary": "Designa a otro titular en su lugar."}
            )
        return attrs


class RoleAssignmentSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(queryset=get_user_model().objects.all())
    tower = serializers.PrimaryKeyRelatedField(queryset=Tower.objects.all())
    user_email = serializers.CharField(source="user.email", read_only=True)

    class Meta:
        model = RoleAssignment
        fields = ("id", "user", "user_email", "role", "tower")
        read_only_fields = ("id", "user_email")
        validators = ()  # un rol repetido lo resuelve el servicio (409)
