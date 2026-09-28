from django.core.management.base import BaseCommand
from portal.tender_grouping import assign_project_group
from portal.tender_models import TenderOpportunity


class Command(BaseCommand):
    help = 'Group verified project identities without deleting notices or changing capture times.'

    def handle(self, **options):
        changed, last = 0, 0
        while rows := list(TenderOpportunity.objects.filter(pk__gt=last).order_by('pk')[:100]):
            for opportunity in rows:
                previous = opportunity.project_group_key
                assign_project_group(opportunity)
                if previous != opportunity.project_group_key:
                    TenderOpportunity.objects.filter(pk=opportunity.pk, updated_at=opportunity.updated_at).update(project_group_key=opportunity.project_group_key)
                    changed += 1
                last = opportunity.pk
        self.stdout.write(f'Updated {changed} project grouping keys; all source notices retained.')
