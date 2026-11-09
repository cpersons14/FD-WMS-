"""Office receiving, immutable posted receipts and a separate email outbox."""
import hashlib
import re
from pathlib import PurePath
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone
from .models import (InboundReceipt, InboundReceiptLine, ReceiptDocument, ReceiptGroup,
                     ReceiptEmail, ReceiptEmailAttempt, Part, Stock, Event)
from .operations import require_warehouse
from .services import WorkflowError, quantity
from .units import convert

MAX_DOCUMENT=6*1024*1024
MAX_TOTAL=12*1024*1024
MAX_FILES=12


def read_uploads(files):
    """Bounded private PDF/JPEG/PNG files; never trust a browser MIME header."""
    if len(files)>MAX_FILES: raise WorkflowError('Use at most 12 documents per receipt.')
    result=[]
    total=0
    for upload in files:
        if upload.size>MAX_DOCUMENT: raise WorkflowError('Each document must be 6 MB or smaller.')
        data=upload.read(MAX_DOCUMENT+1)
        total+=len(data)
        if len(data)>MAX_DOCUMENT or total>MAX_TOTAL: raise WorkflowError('Documents exceed the 12 MB receipt limit.')
        name=PurePath(upload.name.replace('\\','/')).name
        name=re.sub(r'[^\w .()\-]', '_', name)[:180] or 'document'
        suffix=PurePath(name).suffix.lower()
        mime=None
        if suffix=='.pdf' and data.startswith(b'%PDF-'): mime='application/pdf'
        if suffix in ('.jpg','.jpeg') and data.startswith(b'\xff\xd8\xff'): mime='image/jpeg'
        if suffix=='.png' and data.startswith(b'\x89PNG\r\n\x1a\n'): mime='image/png'
        if not mime: raise WorkflowError('Upload scanned PDF, JPG or PNG files with a matching file type.')
        result.append(dict(filename=name,mime_type=mime,size=len(data),content=data,sha256=hashlib.sha256(data).hexdigest()))
    return result


@transaction.atomic
def save_receipt(actor,request_key,receipt_id,revision,header,rows,uploads=(),keep_ids=(),email_ids=(),new_email_indices=(),status='DRAFT'):
    require_warehouse(actor)
    if len(rows)>100: raise WorkflowError('Use at most 100 receipt rows.')
    if status not in ('DRAFT','CLARIFY'): raise WorkflowError('Invalid draft status.')
    if receipt_id:
        receipt=InboundReceipt.objects.select_for_update().get(pk=receipt_id)
    else:
        receipt,created=InboundReceipt.objects.get_or_create(request_key=request_key,defaults={'created_by':actor})
        receipt=InboundReceipt.objects.select_for_update().get(pk=receipt.pk)
        if not created:
            if receipt.created_by_id!=actor.pk: raise WorkflowError('This request key is already used.')
            return receipt  # Repeated New Receipt POST never creates a second delivery.
    if receipt.status=='POSTED': return receipt
    if receipt.revision!=revision: raise WorkflowError('Another user saved this receipt. Reload before editing to avoid losing their changes.')
    docs=list(receipt.documents.defer('content'))
    existing={str(d.pk) for d in docs}
    if not set(keep_ids).issubset(existing) or not set(email_ids).issubset(existing):
        raise WorkflowError('Document selection does not belong to this receipt.')
    retained=[d for d in docs if str(d.pk) in keep_ids]
    if len(retained)+len(uploads)>MAX_FILES or sum(d.size for d in retained)+sum(u['size'] for u in uploads)>MAX_TOTAL:
        raise WorkflowError('A receipt supports up to 12 files and 12 MB total.')
    for field in ('supplier','received_date','reference','notes','group'):
        setattr(receipt,field,header.get(field))
    receipt.draft_lines=rows;receipt.status=status;receipt.revision+=1;receipt.save()
    for doc in docs:
        if str(doc.pk) not in keep_ids: doc.delete()
        else:
            doc.include_in_email=str(doc.pk) in email_ids;doc.save(update_fields=['include_in_email'])
    for i,upload in enumerate(uploads):
        ReceiptDocument.objects.create(receipt=receipt,uploaded_by=actor,include_in_email=str(i) in new_email_indices,**upload)
    Event.objects.create(actor=actor,receipt=receipt,action='RECEIPT_DRAFT',note=f'{receipt.number}; revision {receipt.revision}')
    return receipt


def receipt_body(receipt,lines):
    text=['S3PL / Family Dollar — Receiving confirmation',receipt.number,
          f'Supplier: {receipt.supplier}',f'Received date: {receipt.received_date}',
          f'BOL / packing list / delivery reference: {receipt.reference}','']
    for line in lines:
        entry=line.entry
        text.append(f'{line.sku} | {line.description}')
        text.append(f'Received: {entry["quantity"]} {entry["unit"]} + {entry["loose_eaches"]} loose EA = {line.quantity} EA; location: {line.location}.')
        text.append(f'Case pack: {entry["case_pack"] or "not configured"} EA; cases per pallet: {entry["pallet_cases"] or "not configured"}.')
        text.append('ON HOLD — unavailable for orders.' if line.held else 'Received into available inventory (subject to reservations).')
        if line.note: text.append(f'Line note: {line.note}')
        text.append('')
    if receipt.notes: text.extend(['Delivery exceptions / notes:',receipt.notes,''])
    text.append('Received quantities reflect S3PL office posting from the putaway sheet. Contact S3PL for corrections.')
    return '\n'.join(text)


@transaction.atomic
def post_receipt(actor,receipt_id,revision):
    require_warehouse(actor)
    receipt=InboundReceipt.objects.select_for_update().get(pk=receipt_id)
    if receipt.status=='POSTED': return receipt
    if receipt.revision!=revision: raise WorkflowError('Receipt changed. Review the latest revision before submitting.')
    if not receipt.supplier or not receipt.received_date or not receipt.reference:
        raise WorkflowError('Enter supplier, received date and a delivery reference before submitting.')
    if receipt.received_date>timezone.localdate(): raise WorkflowError('Received date cannot be in the future.')
    if not receipt.group_id: raise WorkflowError('Select the FD Receiving email group before submitting.')
    group=ReceiptGroup.objects.select_for_update().get(pk=receipt.group_id)
    try: recipients=group.recipients()
    except ValidationError: raise WorkflowError('Ask an administrator to correct the receipt group addresses.')
    if not group.active or not recipients: raise WorkflowError('An administrator must configure approved receipt-group addresses before submission.')
    rows=receipt.draft_lines
    if not rows: raise WorkflowError('Add at least one received item.')
    parts={p.sku:p for p in Part.objects.select_for_update().filter(sku__in=[r['sku'] for r in rows]).order_by('pk')}
    stocks={(s.part_id,s.location.code):s for s in Stock.objects.select_for_update().filter(part_id__in=[p.pk for p in parts.values()]).select_related('location').order_by('pk')}
    lines=[]
    for i,row in enumerate(rows,1):
        part=parts.get(row['sku'])
        if not part: raise WorkflowError(f'Row {i}: part number not found.')
        stock=stocks.get((part.pk,row['location']))
        if not stock: raise WorkflowError(f'Row {i}: set up this part/location in Inventory first.')
        if stock.on_hand is None or stock.count_required: raise WorkflowError(f'Row {i}: count {part.sku} / {stock.location.code} before adding a receipt.')
        # The displayed pack values must still match at posting. No silent reconversion.
        if str(part.units_per_case or '')!=row['case_pack'] or str(part.cases_per_pallet or '')!=row['pallet_cases']:
            raise WorkflowError(f'Row {i}: pack settings changed. Review and save the current pack values before submitting.')
        amount,entry=convert(part,row['quantity'],row['unit'],row.get('loose_eaches',0))
        entry.update(case_pack=part.units_per_case,pallet_cases=part.cases_per_pallet)
        if row.get('damaged') and not stock.held: raise WorkflowError(f'Row {i}: damaged stock must go into a held quarantine bin.')
        before=stock.on_hand
        stock.on_hand=quantity(before+amount);stock.save(update_fields=['on_hand'])
        line=InboundReceiptLine.objects.create(receipt=receipt,stock=stock,sku=part.sku,description=part.description,location=stock.location.code,quantity=amount,entry=entry,held=stock.held,note=row.get('note',''))
        lines.append(line)
        Event.objects.create(actor=actor,stock=stock,receipt=receipt,action='RECEIVE',quantity=amount,before=before,after=stock.on_hand,entry=entry,note=f'{receipt.number} | {receipt.reference}')
    receipt.status='POSTED';receipt.posted_at=timezone.now();receipt.posted_by=actor;receipt.revision+=1;receipt.save()
    docs=list(receipt.documents.filter(include_in_email=True).defer('content'))
    ReceiptEmail.objects.create(receipt=receipt,group_name=group.name,recipients=recipients,document_ids=[str(d.pk) for d in docs],document_names=[d.filename for d in docs],subject=f'S3PL FD receipt {receipt.number} | {receipt.received_date}',body=receipt_body(receipt,lines))
    return receipt


def send_receipt_email(receipt_id,actor=None,retry=False,checked_delivery=False):
    """Provider acceptance isn't inbox delivery. Never automatically retry an uncertain send."""
    if actor is not None: require_warehouse(actor)
    if not settings.WMS_RECEIPT_EMAIL_ENABLED: return 'DISABLED'
    if settings.EMAIL_BACKEND=='django.core.mail.backends.smtp.EmailBackend' and (not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL):
        return 'CONFIGURATION_REQUIRED'
    with transaction.atomic():
        email=ReceiptEmail.objects.select_for_update().get(receipt_id=receipt_id)
        if email.status=='SENT': return 'SENT'
        group=ReceiptGroup.objects.get(pk=email.receipt.group_id)
        try: current=group.recipients()
        except ValidationError: return 'GROUP_REVIEW_REQUIRED'
        if not group.active or set(current)!=set(email.recipients): return 'GROUP_REVIEW_REQUIRED'
        if email.status in ('FAILED','SENDING'):
            if not (actor and retry and checked_delivery): return email.status
            if email.status=='SENDING':
                last=email.attempts.first()
                if last and last.started_at>timezone.now()-timedelta(minutes=5):
                    raise WorkflowError('A send is still running. Wait at least five minutes before investigating a retry.')
        email.status='SENDING';email.save(update_fields=['status'])
        attempt=ReceiptEmailAttempt.objects.create(email=email,actor=actor)
    try:
        msg=EmailMessage(email.subject,email.body,settings.DEFAULT_FROM_EMAIL,email.recipients)
        docs=list(ReceiptDocument.objects.filter(receipt_id=receipt_id,pk__in=email.document_ids))
        if len(docs)!=len(email.document_ids): raise RuntimeError('Missing receipt attachment')
        for doc in docs: msg.attach(doc.filename,bytes(doc.content),doc.mime_type)
        if msg.send(fail_silently=False)!=1: raise RuntimeError('Email not accepted')
    except Exception as exc:
        ReceiptEmail.objects.filter(pk=email.pk).update(status='FAILED')
        ReceiptEmailAttempt.objects.filter(pk=attempt.pk).update(status='FAILED',error=type(exc).__name__,finished_at=timezone.now())
        return 'FAILED'
    now=timezone.now()
    ReceiptEmail.objects.filter(pk=email.pk).update(status='SENT',sent_at=now)
    ReceiptEmailAttempt.objects.filter(pk=attempt.pk).update(status='SENT',finished_at=now)
    return 'SENT'
