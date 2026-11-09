from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from warehouse.models import ReceiptGroup


class Command(BaseCommand):
    help='Create WMS roles and the empty receiving group without loading inventory or creating users.'

    @transaction.atomic
    def handle(self,*args,**options):
        for name in ['FD Corporate','S3PL Warehouse']:
            Group.objects.get_or_create(name=name)
        ReceiptGroup.objects.get_or_create(name='FD Receiving')
        self.stdout.write(self.style.SUCCESS('WMS roles ready. No inventory or user accounts were added.'))
