from django.core.management.base import BaseCommand,CommandError
from warehouse.notifications import send_stock_digest

class Command(BaseCommand):
    help='Send one low-stock digest per enabled portal user per local day. Email disabled by default.'
    def add_arguments(self,parser): parser.add_argument('--dry-run',action='store_true')
    def handle(self,*args,**options):
        result=send_stock_digest(dry_run=options['dry_run'])
        self.stdout.write(str(result))
        if result.get('failed'): raise CommandError('One or more notifications failed. Inspect Alert deliveries.')
