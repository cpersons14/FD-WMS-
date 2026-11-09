"""Daily customer stock digest. Disabled until IT configures and enables SMTP."""
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from .models import AlertSubscription, AlertDelivery
from .reporting import stock_summary
from .units import breakdown

def fd_recipient(user):
    return user.is_authenticated and user.is_active and user.groups.filter(name='FD Corporate').exists()


def digest_text(rows):
    lines=['S3PL / Family Dollar — low available stock',
           'Available excludes reservations, picked stock, quality holds and uncounted bins.',
           'These are restock recommendations, not purchase orders.','']
    for r in rows:
        p=r['part']
        lines.append(f'{p.sku} | {p.description} | unit: {p.unit or "UNVERIFIED"}')
        lines.append(f'Pack size: {p.pack_description}; available equivalents: {breakdown(p,r["available"])}')
        lines.append(f'Available: {r["available"]}; threshold: {p.low_stock_threshold}; reserved: {r["reserved"]}; backordered: {r["backordered"]}; held: {r["held"]}; uncounted bins: {r["unknown"]}.')
        if r['reorder'] is not None: lines.append(f'Suggested restock: {r["reorder"]} (excludes expected inbound shipments).')
    return '\n'.join(lines)


def send_stock_digest(dry_run=False):
    rows=[r for r in stock_summary() if r['low']]
    body=digest_text(rows)
    if dry_run: return {'preview':body,'items':len(rows),'sent':0}
    if not settings.WMS_EMAIL_ENABLED: return {'disabled':True,'sent':0}
    if not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL:
        if settings.EMAIL_BACKEND=='django.core.mail.backends.smtp.EmailBackend':
            raise RuntimeError('Configure EMAIL_HOST and DEFAULT_FROM_EMAIL before enabling WMS email.')
    if not rows: return {'sent':0,'items':0}
    sent=0;failures=0;today=timezone.localdate()
    for subscription in AlertSubscription.objects.filter(enabled=True).select_related('user'):
        user=subscription.user
        if not fd_recipient(user): continue
        try: validate_email(user.email)
        except ValidationError: continue
        # Claim persistently before sending. A crash after provider acceptance must not blindly resend.
        with transaction.atomic():
            sub=AlertSubscription.objects.select_for_update().get(pk=subscription.pk)
            delivery,created=AlertDelivery.objects.get_or_create(subscription=sub,day=today)
            if not created: continue
            delivery.status='SENDING';delivery.save(update_fields=['status'])
        try:
            result=EmailMessage(f'S3PL: {len(rows)} low-stock items',body,settings.DEFAULT_FROM_EMAIL,[user.email]).send(fail_silently=False)
            if result!=1: raise RuntimeError('Email backend did not accept the message.')
        except Exception as exc:
            delivery.status='FAILED';delivery.error=type(exc).__name__;delivery.save(update_fields=['status','error']);failures+=1
        else:
            delivery.status='SENT';delivery.sent_at=timezone.now();delivery.save(update_fields=['status','sent_at'])
            sub.last_digest_date=today;sub.save(update_fields=['last_digest_date']);sent+=1
    return {'sent':sent,'failed':failures,'items':len(rows)}
