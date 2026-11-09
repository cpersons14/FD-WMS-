import json
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from warehouse.models import Part,Location,Stock,Event
from warehouse.services import quantity, WorkflowError

class Command(BaseCommand):
    help='Import the initial FD inventory snapshot once. Refuses to overwrite inventory.'
    def add_arguments(self,parser):
        parser.add_argument('file',type=Path)
        parser.add_argument('--actor',required=True)
    @transaction.atomic
    def handle(self,*args,**options):
        if Stock.objects.exists(): raise CommandError('Inventory already exists. Import refused to protect warehouse activity.')
        try: actor=get_user_model().objects.get(username=options['actor'])
        except get_user_model().DoesNotExist: raise CommandError('Create the importing administrator first.')
        if not actor.is_superuser: raise CommandError('The importing actor must be a superuser.')
        rows=json.loads(options['file'].read_text());seen=set()
        for row in rows:
            key=(row['sku'],row['location'])
            if key in seen or not all(key): raise CommandError(f'Duplicate or missing part/location at row {row["row"]}.')
            seen.add(key)
            if row['kind'] not in ('PICK','OVERSTOCK'): raise CommandError('Invalid location type.')
            part,_=Part.objects.get_or_create(sku=row['sku'],defaults={'description':row['description']})
            if not part.description and row['description']:
                part.description=row['description'];part.save()
            location,_=Location.objects.get_or_create(code=row['location'])
            source_note=''
            try:
                amount=None if row['on_hand'] is None else quantity(row['on_hand'],allow_zero=True)
            except WorkflowError:
                amount=None
                source_note=f'; invalid source quantity {row["on_hand"]!r} retained for review'
            stock=Stock.objects.create(part=part,location=location,kind=row['kind'],on_hand=amount,source_row=row['row'],count_required=amount is None or row.get('count_required',False))
            if row.get('source_note'): source_note+='; '+row['source_note']
            Event.objects.create(actor=actor,stock=stock,action='OPENING_IMPORT',quantity=amount or 0,after=amount,note=f'FAMILY DOLLAR worksheet row {row["row"]}; blank = uncounted{source_note}')
        for name in ['FD Corporate','S3PL Warehouse']: Group.objects.get_or_create(name=name)
        self.stdout.write(self.style.SUCCESS(f'Imported {len(rows)} bin records; {Part.objects.count()} parts; {Stock.objects.filter(count_required=True).count()} need counts.'))
