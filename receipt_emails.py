from django.core.management.base import BaseCommand
from warehouse.models import ReceiptEmail
from warehouse.receiving import send_receipt_email

class Command(BaseCommand):
    help='Process pending receipt emails only. Failed/uncertain attempts require office review.'
    def add_arguments(self,parser): parser.add_argument('--dry-run',action='store_true')
    def handle(self,*args,**options):
        for email in ReceiptEmail.objects.filter(status='PENDING').select_related('receipt').iterator():
            result='PENDING (not sent)' if options['dry_run'] else send_receipt_email(email.receipt_id)
            self.stdout.write(f'{email.receipt.number}: {result}')
