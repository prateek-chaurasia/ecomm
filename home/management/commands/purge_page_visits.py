from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from home.models import PageVisit


class Command(BaseCommand):
    help = 'Delete visitor page-view records older than 90 days.'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=90)
        deleted_count, _ = PageVisit.objects.filter(visited_at__lt=cutoff).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f'Deleted {deleted_count} expired visitor page-view record(s).'
            )
        )
