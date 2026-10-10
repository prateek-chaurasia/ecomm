import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from home.models import City, State


class Command(BaseCommand):
    help = 'Import state-scoped cities from a CSV with state and city columns.'

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, type=Path)

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = options['file']
        if not csv_path.is_file():
            raise CommandError(f'CSV file not found: {csv_path}')

        created_count = 0
        existing_count = 0
        with csv_path.open(newline='', encoding='utf-8-sig') as csv_file:
            reader = csv.DictReader(csv_file)
            if not reader.fieldnames or not {'state', 'city'}.issubset(
                {field.strip().casefold() for field in reader.fieldnames}
            ):
                raise CommandError('CSV must contain state and city columns.')

            for row_number, row in enumerate(reader, start=2):
                values = {key.strip().casefold(): value for key, value in row.items() if key}
                state_name = (values.get('state') or '').strip()
                city_name = ' '.join((values.get('city') or '').split())
                normalized_name = City.normalize_name(city_name)
                if not state_name or not city_name or not normalized_name:
                    raise CommandError(f'Row {row_number} must include a state and city.')

                state = State.objects.filter(
                    country__code='IN', name__iexact=state_name,
                ).first()
                if not state:
                    raise CommandError(
                        f'Row {row_number} references unknown Indian state: {state_name}'
                    )

                _, created = City.objects.get_or_create(
                    state=state,
                    normalized_name=normalized_name,
                    defaults={'name': city_name},
                )
                if created:
                    created_count += 1
                else:
                    existing_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Imported {created_count} cities; skipped {existing_count} normalized duplicates.'
        ))