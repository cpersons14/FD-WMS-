import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q, F

class Part(models.Model):
    sku=models.CharField(max_length=100,unique=True)
    description=models.CharField(max_length=500,blank=True)
    barcode=models.CharField(max_length=100,unique=True,null=True,blank=True)
    unit=models.CharField(max_length=24,blank=True,help_text='Confirm EA only when existing inventory quantities already represent eaches. This does not rescale balances.')
    units_per_case=models.PositiveIntegerField(null=True,blank=True,help_text='Confirmed eaches in one case; never guessed.')
    cases_per_pallet=models.PositiveIntegerField(null=True,blank=True,help_text='Standard full pallet only; use cases/eaches for a partial or variable pallet.')
    low_stock_threshold=models.DecimalField(max_digits=16,decimal_places=3,null=True,blank=True)
    target_stock=models.DecimalField(max_digits=16,decimal_places=3,null=True,blank=True)
    pick_min=models.DecimalField(max_digits=16,decimal_places=3,null=True,blank=True)
    count_interval_days=models.PositiveIntegerField(default=90)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(units_per_case__isnull=True)|Q(units_per_case__gt=0),name='positive_case_pack'),models.CheckConstraint(condition=Q(cases_per_pallet__isnull=True)|Q(cases_per_pallet__gt=0),name='positive_pallet_pack'),models.CheckConstraint(condition=Q(cases_per_pallet__isnull=True)|Q(units_per_case__isnull=False),name='pallet_requires_case_pack')]
    @property
    def pack_description(self):
        if self.unit!='EA': return 'Base unit needs confirmation'
        if not self.units_per_case: return 'Case pack not configured'
        text=f'{self.units_per_case} EA / case'
        return text+(f'; {self.cases_per_pallet} cases / pallet' if self.cases_per_pallet else '; pallet pack not configured')
    def __str__(self): return self.sku

class Location(models.Model):
    code=models.CharField(max_length=100,unique=True)
    def __str__(self): return self.code

class Stock(models.Model):
    part=models.ForeignKey(Part,on_delete=models.PROTECT)
    location=models.ForeignKey(Location,on_delete=models.PROTECT)
    kind=models.CharField(max_length=12,choices=[('PICK','Pick'),('OVERSTOCK','Overstock')])
    on_hand=models.DecimalField(max_digits=16,decimal_places=3,null=True,blank=True)
    count_required=models.BooleanField(default=False)
    reserved=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    held=models.BooleanField(default=False)
    hold_reason=models.CharField(max_length=500,blank=True)
    last_counted_at=models.DateTimeField(null=True,blank=True)
    source_row=models.PositiveIntegerField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['part','location'],name='unique_part_location'),
         models.CheckConstraint(condition=Q(on_hand__isnull=True)|Q(on_hand__gte=0),name='nonnegative_stock'),
         models.CheckConstraint(condition=Q(reserved__gte=0),name='nonnegative_reserved'),
         models.CheckConstraint(condition=Q(on_hand__isnull=False,reserved__lte=F('on_hand'))|Q(on_hand__isnull=True,reserved=0),name='reservation_within_stock')]
    @property
    def available(self): return None if self.on_hand is None or self.count_required else (0 if self.held else self.on_hand-self.reserved)
    @property
    def available_packs(self):
        from .units import breakdown
        return breakdown(self.part,self.available)
    def __str__(self): return f'{self.part} / {self.location}'

class Order(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    request_key=models.UUIDField(unique=True)
    store=models.CharField(max_length=30)
    recipient=models.CharField(max_length=150)
    address=models.CharField(max_length=250)
    city=models.CharField(max_length=100)
    state=models.CharField(max_length=2)
    postal_code=models.CharField(max_length=12)
    reference=models.CharField(max_length=150,blank=True)
    notes=models.TextField(blank=True)
    status=models.CharField(max_length=12,default='SUBMITTED',choices=[('SUBMITTED','Submitted'),('PICKED','Picked'),('SHIPPED','Shipped'),('CANCELLED','Cancelled'),('PARTIAL','Partial / backordered'),('PACKED','Packed')])
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    allow_backorder=models.BooleanField(default=False)
    priority=models.CharField(max_length=10,default='NORMAL',choices=[('NORMAL','Normal'),('URGENT','Urgent')])
    requested_ship_date=models.DateField(null=True,blank=True)
    carrier=models.CharField(max_length=100,blank=True)
    tracking=models.CharField(max_length=150,blank=True)
    shipped_at=models.DateTimeField(null=True,blank=True)
    @property
    def number(self): return 'FD-'+self.id.hex[:8].upper()
    class Meta:
        ordering=['-created_at']

class OrderLine(models.Model):
    order=models.ForeignKey(Order,on_delete=models.PROTECT,related_name='lines')
    part=models.ForeignKey(Part,on_delete=models.PROTECT)
    quantity=models.DecimalField(max_digits=16,decimal_places=3)
    base_unit=models.CharField(max_length=24,blank=True)
    case_pack_snapshot=models.PositiveIntegerField(null=True,blank=True)
    pallet_cases_snapshot=models.PositiveIntegerField(null=True,blank=True)
    requested_packs=models.JSONField(default=list,blank=True)
    cancelled=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    picked=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    packed=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    shipped=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    returned=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    @property
    def reserved_quantity(self):
        return sum((a.quantity for a in self.allocations.all() if a.state=='RESERVED'),0)
    @property
    def backordered(self):
        return 0 if self.order.status=='CANCELLED' else self.quantity-self.cancelled-self.picked-self.reserved_quantity
    @property
    def staged(self): return self.picked-self.shipped
    @property
    def requested_display(self):
        return ' + '.join(f"{r['quantity']} {r['unit']}" for r in self.requested_packs) if self.requested_packs else f'{self.quantity} {self.base_unit or "recorded base units"}'
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(picked__gte=0,packed__gte=0,shipped__gte=0,returned__gte=0,cancelled__gte=0)&Q(picked__lte=F('quantity')-F('cancelled'),packed__lte=F('picked'),shipped__lte=F('packed'),returned__lte=F('shipped')),name='line_fulfillment_bounds'),models.UniqueConstraint(fields=['order','part'],name='unique_order_part'),models.CheckConstraint(condition=Q(quantity__gt=0),name='positive_order_quantity')]

class Allocation(models.Model):
    state=models.CharField(max_length=12,default='RESERVED',choices=[('RESERVED','Reserved'),('PICKED','Picked'),('RELEASED','Released')])
    line=models.ForeignKey(OrderLine,on_delete=models.PROTECT,related_name='allocations')
    stock=models.ForeignKey(Stock,on_delete=models.PROTECT)
    quantity=models.DecimalField(max_digits=16,decimal_places=3)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(quantity__gt=0),name='positive_allocation')]

class Event(models.Model):
    at=models.DateTimeField(auto_now_add=True)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    stock=models.ForeignKey(Stock,on_delete=models.PROTECT,null=True,blank=True)
    order=models.ForeignKey(Order,on_delete=models.PROTECT,null=True,blank=True)
    receipt=models.ForeignKey('InboundReceipt',on_delete=models.PROTECT,null=True,blank=True)
    action=models.CharField(max_length=40)
    quantity=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    before=models.DecimalField(max_digits=16,decimal_places=3,null=True,blank=True)
    after=models.DecimalField(max_digits=16,decimal_places=3,null=True,blank=True)
    entry=models.JSONField(default=dict,blank=True)
    note=models.TextField(blank=True)
    class Meta:
        ordering=['-at','-id']


class Shipment(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    request_key=models.UUIDField(unique=True)
    order=models.ForeignKey(Order,on_delete=models.PROTECT,related_name='shipments')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    carrier=models.CharField(max_length=100)
    tracking=models.CharField(max_length=150)

class ShipmentLine(models.Model):
    shipment=models.ForeignKey(Shipment,on_delete=models.PROTECT,related_name='lines')
    line=models.ForeignKey(OrderLine,on_delete=models.PROTECT)
    quantity=models.DecimalField(max_digits=16,decimal_places=3)

class ReturnReceipt(models.Model):
    entry=models.JSONField(default=dict,blank=True)
    request_key=models.UUIDField(unique=True)
    line=models.ForeignKey(OrderLine,on_delete=models.PROTECT)
    stock=models.ForeignKey(Stock,on_delete=models.PROTECT)
    quantity=models.DecimalField(max_digits=16,decimal_places=3)
    reason=models.CharField(max_length=500)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)

class OperationKey(models.Model):
    key=models.UUIDField(primary_key=True)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    fingerprint=models.TextField()
    created_at=models.DateTimeField(auto_now_add=True)

class AlertSubscription(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    enabled=models.BooleanField(default=False)
    last_digest_date=models.DateField(null=True,blank=True)

class AlertDelivery(models.Model):
    subscription=models.ForeignKey(AlertSubscription,on_delete=models.CASCADE)
    day=models.DateField()
    status=models.CharField(max_length=12,default='PENDING',choices=[('PENDING','Pending'),('SENDING','Sending'),('SENT','Sent'),('FAILED','Failed')])
    error=models.CharField(max_length=500,blank=True)
    sent_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['subscription','day'],name='one_digest_per_day')]


class ReceiptGroup(models.Model):
    name=models.CharField(max_length=100,unique=True,default='FD Receiving')
    addresses=models.TextField(blank=True,help_text='Approved FD distribution address, or one approved email per line. No display names.')
    active=models.BooleanField(default=True)
    def recipients(self):
        from django.core.validators import validate_email
        values=list(dict.fromkeys(x.strip() for x in self.addresses.replace(',', '\n').splitlines() if x.strip()))
        if len(values)>30:
            from django.core.exceptions import ValidationError
            raise ValidationError('Use a distribution list for more than 30 recipients.')
        for value in values: validate_email(value)
        return values
    def clean(self): self.recipients()
    def __str__(self): return self.name


class InboundReceipt(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    request_key=models.UUIDField(unique=True)
    supplier=models.CharField(max_length=150,blank=True)
    received_date=models.DateField(null=True,blank=True)
    reference=models.CharField(max_length=150,blank=True)
    notes=models.TextField(blank=True)
    status=models.CharField(max_length=12,default='DRAFT',choices=[('DRAFT','Draft'),('CLARIFY','Needs clarification'),('POSTED','Posted')])
    group=models.ForeignKey(ReceiptGroup,on_delete=models.PROTECT,null=True,blank=True)
    draft_lines=models.JSONField(default=list,blank=True)
    revision=models.PositiveIntegerField(default=0)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='inbound_receipts')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    posted_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='posted_receipts')
    posted_at=models.DateTimeField(null=True,blank=True)
    @property
    def number(self): return 'RCV-'+self.id.hex.upper()
    class Meta:
        ordering=['-created_at']


class InboundReceiptLine(models.Model):
    receipt=models.ForeignKey(InboundReceipt,on_delete=models.PROTECT,related_name='lines')
    stock=models.ForeignKey(Stock,on_delete=models.PROTECT)
    sku=models.CharField(max_length=100)
    description=models.CharField(max_length=500,blank=True)
    location=models.CharField(max_length=100)
    quantity=models.DecimalField(max_digits=16,decimal_places=3)
    entry=models.JSONField(default=dict)
    held=models.BooleanField(default=False)
    note=models.CharField(max_length=500,blank=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(quantity__gt=0),name='positive_inbound_quantity')]


class ReceiptDocument(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    receipt=models.ForeignKey(InboundReceipt,on_delete=models.PROTECT,related_name='documents')
    filename=models.CharField(max_length=180)
    mime_type=models.CharField(max_length=60)
    size=models.PositiveIntegerField()
    # Private, bounded documents survive deploys and are included in database backups.
    content=models.BinaryField()
    sha256=models.CharField(max_length=64)
    include_in_email=models.BooleanField(default=True)
    uploaded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    uploaded_at=models.DateTimeField(auto_now_add=True)


class ReceiptEmail(models.Model):
    receipt=models.OneToOneField(InboundReceipt,on_delete=models.PROTECT,related_name='email')
    group_name=models.CharField(max_length=100)
    recipients=models.JSONField(default=list)
    document_ids=models.JSONField(default=list)
    document_names=models.JSONField(default=list)
    subject=models.CharField(max_length=250)
    body=models.TextField()
    status=models.CharField(max_length=12,default='PENDING',choices=[('PENDING','Pending'),('SENDING','Sending / check delivery'),('SENT','Sent'),('FAILED','Failed / check delivery')])
    sent_at=models.DateTimeField(null=True,blank=True)


class ReceiptEmailAttempt(models.Model):
    email=models.ForeignKey(ReceiptEmail,on_delete=models.PROTECT,related_name='attempts')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    started_at=models.DateTimeField(auto_now_add=True)
    finished_at=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=12,default='SENDING')
    error=models.CharField(max_length=100,blank=True)
    class Meta:
        ordering=['-started_at']
