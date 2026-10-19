"""Genera las cuotas ordinarias de un periodo. Se programa con cron el día 1."""

import re
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.finances.services import FinancesError, generate_monthly_charges
from apps.properties.models import Tower

PERIOD_PATTERN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class Command(BaseCommand):
    help = (
        "Genera las cuotas ordinarias del periodo (AAAA-MM). Es repetible sin duplicar."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--period", help="Periodo AAAA-MM (por defecto, el mes actual)."
        )
        parser.add_argument("--tower", help="Nombre de la torre (por defecto, todas).")

    def handle(self, *args, **options):
        period = self._period(options["period"])
        tower = self._tower(options["tower"])
        try:
            result = generate_monthly_charges(
                period, tower=tower, actor_label="comando generate_charges"
            )
        except FinancesError as error:
            raise CommandError(str(error)) from error

        if result.skipped:
            self.stderr.write(
                f"Aviso: {result.skipped} unidades sin inicio de cobro vigente no se cobraron."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.period:%Y-%m}: {result.created} cargos nuevos, "
                f"{result.existing} ya existían."
            )
        )

    def _period(self, raw):
        if raw is None:
            return timezone.localdate().replace(day=1)
        match = PERIOD_PATTERN.match(raw)
        if not match:
            raise CommandError(
                "El periodo debe tener el formato AAAA-MM, por ejemplo 2026-11."
            )
        return date(int(match[1]), int(match[2]), 1)

    def _tower(self, name):
        if not name:
            return None
        towers = list(Tower.objects.filter(name__iexact=name))
        if len(towers) != 1:
            raise CommandError(f"No se encontró una única Torre llamada «{name}».")
        return towers[0]
