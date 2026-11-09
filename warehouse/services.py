from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from .models import Part, Stock, Order, OrderLine, Allocation, Event

class WorkflowError(ValueError): pass

def quantity(value, allow_zero=False):
    try: q=Decimal(str(value))
    except (InvalidOperation, ValueError): raise WorkflowError('Enter a valid quantity.')
    if not q.is_finite() or q < 0 or (q==0 and not allow_zero) or q.as_tuple().exponent < -3 or q>Decimal('9999999999999'):
        raise WorkflowError('Quantity must be positive, with at most three decimal places (zero is allowed for a count).')
    return q

def warehouse_user(user):
    return user.is_active and (user.is_superuser or user.groups.filter(name='S3PL Warehouse').exists())

def portal_user(user):
    return user.is_authenticated and user.is_active and (warehouse_user(user) or user.groups.filter(name='FD Corporate').exists())

@transaction.atomic
def submit_order(actor, details, items, request_key):
    if not portal_user(actor): raise PermissionError('Portal access required.')
    existing=Order.objects.filter(request_key=request_key).first()
    if existing:
        if existing.created_by_id != actor.pk: raise WorkflowError('Submission key already used.')
        return existing
    if not items: raise WorkflowError('Add at least one part.')
    merged={}
    for item in items:
        sku,qty=item[:2]
        unit=item[2] if len(item)>2 else 'BASE'
        merged.setdefault(sku,[]).append((qty,unit))
    # Lock the part as a common serialization point across order, count and transfer operations.
    parts=list(Part.objects.select_for_update().filter(sku__in=merged).order_by('pk'))
    if len(parts)!=len(merged): raise WorkflowError('One or more parts were not found.')
    # Recheck after waiting for locks: duplicate browser submissions must not reserve twice.
    existing=Order.objects.filter(request_key=request_key).first()
    if existing:
        if existing.created_by_id != actor.pk: raise WorkflowError('Submission key already used.')
        return existing
    order=Order.objects.create(created_by=actor,request_key=request_key,**details)
    for part in parts:
        from .units import convert
        converted=[convert(part,q,u) for q,u in merged[part.sku]]
        total=quantity(sum((q for q,_ in converted),Decimal(0)))
        line=OrderLine.objects.create(order=order,part=part,quantity=total,base_unit=part.unit,case_pack_snapshot=part.units_per_case,pallet_cases_snapshot=part.cases_per_pallet,requested_packs=[entry for _,entry in converted])
        reserve_line(actor,line,strict=not order.allow_backorder)
    from .operations import refresh_status
    refresh_status(order)
    Event.objects.create(actor=actor,order=order,action='ORDER_SUBMITTED')
    return order

def reserve_line(actor,line,strict=False):
    wanted=line.backordered
    if wanted<=0: return
    stocks=list(Stock.objects.select_for_update().filter(part=line.part,on_hand__isnull=False,count_required=False,held=False).select_related('location').order_by('pk'))
    if strict and sum((s.available for s in stocks),Decimal(0))<wanted:
        raise WorkflowError(f'{line.part.sku}: insufficient counted inventory. Uncounted or held stock cannot be reserved.')
    for stock in sorted(stocks,key=lambda s:(s.kind!='PICK',s.location.code)):
        take=min(stock.available,wanted)
        if take<=0: continue
        stock.reserved+=take;stock.save(update_fields=['reserved'])
        Allocation.objects.create(line=line,stock=stock,quantity=take)
        Event.objects.create(actor=actor,stock=stock,order=line.order,action='RESERVE',quantity=take,before=stock.on_hand,after=stock.on_hand)
        wanted-=take
        if wanted==0: break


def transition_order(actor,order_id,action,carrier='',tracking='',**kwargs):
    from .operations import fulfill
    return fulfill(actor,order_id,action,carrier,tracking,**kwargs)

@transaction.atomic
def update_stock(actor, stock_id, mode, amount, note, key=None, unit='BASE', loose_eaches=0):
    if not warehouse_user(actor): raise PermissionError('Warehouse access required.')
    if not note.strip(): raise WorkflowError('Enter a reason or receiving reference.')
    part_id=Stock.objects.values_list('part_id',flat=True).get(pk=stock_id)
    part=Part.objects.select_for_update().get(pk=part_id)
    stock=Stock.objects.select_for_update().get(pk=stock_id)
    from .operations import once
    if once(actor,key,action=mode,stock=stock_id,amount=amount,note=note,unit=unit,loose_eaches=loose_eaches): return stock
    before=stock.on_hand
    from .units import convert
    converted,entry=convert(part,amount,unit,loose_eaches,allow_zero=mode=='count')
    if mode=='count': after=converted
    elif mode=='receive':
        if before is None or stock.count_required: raise WorkflowError('Count this bin before adding a receipt.')
        after=quantity(before+converted,allow_zero=True)
    else: raise WorkflowError('Invalid inventory action.')
    if after<stock.reserved: raise WorkflowError('Count is below reserved stock. Resolve the affected orders first.')
    stock.on_hand=after;stock.count_required=False
    if mode=='count': stock.last_counted_at=timezone.now()
    stock.save(update_fields=['on_hand','count_required','last_counted_at'])
    Event.objects.create(actor=actor,stock=stock,action=mode.upper(),quantity=after-(before or 0),before=before,after=after,note=note,entry=entry)
    return stock

@transaction.atomic
def transfer_stock(actor, source_id, target_id, amount, note, key=None, unit='BASE', loose_eaches=0):
    if not warehouse_user(actor): raise PermissionError('Warehouse access required.')
    if source_id==target_id: raise WorkflowError('Choose a different destination.')
    if not note.strip(): raise WorkflowError('Enter a transfer reason.')
    initial=list(Stock.objects.filter(pk__in=[source_id,target_id]).order_by('pk'))
    if len(initial)!=2 or initial[0].part_id!=initial[1].part_id: raise WorkflowError('Both bins must contain the same part.')
    part=Part.objects.select_for_update().get(pk=initial[0].part_id)
    stocks={s.pk:s for s in Stock.objects.select_for_update().filter(pk__in=[source_id,target_id]).order_by('pk')}
    from .operations import once
    if once(actor,key,action='transfer',source=source_id,target=target_id,amount=amount,note=note,unit=unit,loose_eaches=loose_eaches): return
    from .units import convert
    qty,entry=convert(part,amount,unit,loose_eaches)
    src,dst=stocks[source_id],stocks[target_id]
    if src.on_hand is None or dst.on_hand is None or src.count_required or dst.count_required: raise WorkflowError('Count both bins before transferring.')
    if src.held or dst.held: raise WorkflowError('Release the hold after inspection before transferring stock.')
    if qty>src.on_hand: raise WorkflowError('Transfer exceeds on-hand stock.')
    # Move reservations with the physical stock, so allocated overstock can be replenished.
    move_reserved=max(Decimal(0),qty-src.available)
    remaining=move_reserved
    for a in Allocation.objects.select_for_update().filter(stock=src,state='RESERVED').order_by('pk'):
        moved=min(remaining,a.quantity)
        if moved<=0: break
        Allocation.objects.create(line=a.line,stock=dst,quantity=moved)
        if moved==a.quantity: a.delete()
        else: a.quantity-=moved;a.save(update_fields=['quantity'])
        remaining-=moved
    if remaining: raise WorkflowError('Reservation mismatch. Transfer cancelled.')
    for stock,delta,reserve_delta in [(src,-qty,-move_reserved),(dst,qty,move_reserved)]:
        before=stock.on_hand;stock.on_hand+=delta;stock.reserved+=reserve_delta;stock.save()
        Event.objects.create(actor=actor,stock=stock,action='TRANSFER',quantity=delta,before=before,after=stock.on_hand,note=f'{src.location_id} to {dst.location_id}: {note}',entry=entry)
