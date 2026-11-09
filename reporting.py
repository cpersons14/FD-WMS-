from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from .models import Part, Stock, OrderLine


def stock_summary():
    staged={}
    backlog={}
    for line in OrderLine.objects.exclude(order__status='CANCELLED').prefetch_related('allocations','order'):
        staged[line.part_id]=staged.get(line.part_id,0)+line.staged
        backlog[line.part_id]=backlog.get(line.part_id,0)+line.backordered
    rows=[]
    for part in Part.objects.prefetch_related('stock_set').order_by('sku'):
        stocks=list(part.stock_set.all())
        known=[s for s in stocks if s.on_hand is not None and not s.count_required]
        eligible=[s for s in known if not s.held]
        available=sum((s.available for s in eligible),Decimal(0))
        pick=sum((s.on_hand for s in eligible if s.kind=='PICK'),Decimal(0))
        over=sum((s.on_hand for s in eligible if s.kind=='OVERSTOCK'),Decimal(0))
        reserved_over=sum((s.reserved for s in eligible if s.kind=='OVERSTOCK'),Decimal(0))
        low=part.low_stock_threshold is not None and available<=part.low_stock_threshold
        rows.append(dict(part=part,on_hand=sum((s.on_hand for s in known),Decimal(0)),
            available=available,reserved=sum((s.reserved for s in stocks),Decimal(0)),
            held=sum((s.on_hand for s in known if s.held),Decimal(0)),unknown=len(stocks)-len(known),
            staged=staged.get(part.pk,0),backordered=backlog.get(part.pk,0),low=low,
            reorder=max(Decimal(0),(part.target_stock or 0)-available+backlog.get(part.pk,0)) if low and part.target_stock is not None else None,
            replenish=min(over,max(Decimal(0),(part.pick_min or 0)-pick,reserved_over)),
            overstock=next((s for s in eligible if s.kind=='OVERSTOCK' and s.on_hand>0),None)))
    return rows


def due_counts():
    now=timezone.now()
    return [s for s in Stock.objects.select_related('part','location').order_by('location__code','part__sku')
            if s.count_required or s.on_hand is None or s.last_counted_at is None
            or s.last_counted_at+timedelta(days=s.part.count_interval_days)<=now]
