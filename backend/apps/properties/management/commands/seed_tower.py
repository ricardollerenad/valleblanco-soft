"""Crea el condominio, la torre y sus unidades. Se puede repetir sin duplicar."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.properties.models import Condominium, Tower, Unit


class Command(BaseCommand):
    help = "Crea el condominio, la torre y sus unidades (código = piso + número de 2 cifras)."

    def add_arguments(self, parser):
        parser.add_argument("--condominium", default="ValleBlanco")
        parser.add_argument("--tower", default="Torre 2")
        parser.add_argument("--floors", type=int, default=10)
        parser.add_argument("--per-floor", type=int, default=8)

    @transaction.atomic
    def handle(self, *args, **options):
        floors, per_floor = options["floors"], options["per_floor"]
        if floors < 1 or per_floor < 1:
            raise CommandError("--floors y --per-floor deben ser al menos 1.")

        condominium, _ = Condominium.objects.get_or_create(name=options["condominium"])
        tower, _ = Tower.objects.get_or_create(
            condominium=condominium,
            name=options["tower"],
            defaults={"floors": floors},
        )
        created = 0
        for floor in range(1, floors + 1):
            for number in range(1, per_floor + 1):
                _, was_created = Unit.objects.get_or_create(
                    tower=tower,
                    code=f"{floor}{number:02d}",
                    defaults={"floor": floor, "number": number},
                )
                created += was_created
        self.stdout.write(
            self.style.SUCCESS(
                f"{tower.name}: {created} unidades nuevas de {floors * per_floor}."
            )
        )
