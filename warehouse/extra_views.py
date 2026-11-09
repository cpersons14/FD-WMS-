import csv
import uuid
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.views.decorators.http import require_POST
from django.utils import timezone
from .views import access,ctx
from .models import Part,Stock,Location,Event,Order,OrderLine,AlertSubscription
from .forms import PartForm,NewBinForm
from .services import WorkflowError
from .notifications import fd_recipient
from .operations import set_hold,receive_return
from .reporting import stock_summary,due_counts


@access()
def alerts(request):
    subscription,_=AlertSubscription.objects.get_or_create(user=request.user)
    if request.method=='POST':
        enabled=request.POST.get('enabled')=='on'
        if enabled and not fd_recipient(request.user):
            messages.error(request,'Customer stock emails are limited to FD Corporate accounts.')
        elif enabled and not request.user.email:
            messages.error(request,'Ask an administrator to add your approved email address first.')
        else:
            subscription.enabled=enabled;subscription.save(update_fields=['enabled'])
            messages.success(request,'Email preference saved. Delivery requires an enabled company email sender and scheduled digest.')
        return redirect('alerts')
    rows=stock_summary()
    return render(request,'alerts.html',ctx(request,rows=[r for r in rows if r['low']],unconfigured=sum(r['part'].low_stock_threshold is None for r in rows),subscription=subscription,is_fd=fd_recipient(request.user)))


@access(warehouse=True)
def work_queue(request):
    rows=stock_summary()
    return render(request,'work_queue.html',ctx(request,replenishment=[r for r in rows if r['replenish']>0],backorders=[r for r in rows if r['backordered']>0],counts=due_counts(),held=Stock.objects.filter(held=True).select_related('part','location')))


@access(warehouse=True)
def part_settings(request,pk):
    if request.method=='POST':
        with transaction.atomic():
            part=get_object_or_404(Part.objects.select_for_update(),pk=pk)
            before={field:getattr(part,field) for field in PartForm.Meta.fields}
            form=PartForm(request.POST,instance=part)
            if form.is_valid():
                part=form.save()
                changes='; '.join(f'{field}: {before[field]} -> {getattr(part,field)}' for field in PartForm.Meta.fields if before[field]!=getattr(part,field))
                Event.objects.create(actor=request.user,action='ITEM_SETTINGS',note=f'{part.sku}: {changes}')
                messages.success(request,'Item pack settings saved. Existing orders retain their original conversion factors.')
                return redirect('inventory')
    else:
        part=get_object_or_404(Part,pk=pk);form=PartForm(instance=part)
    return render(request,'generic_form.html',ctx(request,title=f'Item settings · {part.sku}',form=form))


@access(warehouse=True)
def new_bin(request):
    form=NewBinForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        with transaction.atomic():
            part,_=Part.objects.get_or_create(sku=d['sku'],defaults={'description':d['description']})
            part=Part.objects.select_for_update().get(pk=part.pk)
            location,_=Location.objects.get_or_create(code=d['location'])
            if part.unit=='EA' and d['opening_count'] is not None and d['opening_count']!=d['opening_count'].to_integral_value(): form.add_error('opening_count','Enter a whole number of eaches.')
            elif Stock.objects.filter(part=part,location=location).exists(): form.add_error('location','This part already has a record in this location.')
            else:
                s=Stock.objects.create(part=part,location=location,kind=d['kind'],on_hand=d['opening_count'],count_required=d['opening_count'] is None,held=d['held'],hold_reason=d['note'] if d['held'] else '',last_counted_at=timezone.now() if d['opening_count'] is not None else None)
                Event.objects.create(actor=request.user,stock=s,action='BIN_OPENED',quantity=s.on_hand or 0,after=s.on_hand,note=d['note'])
                return redirect('inventory')
    return render(request,'generic_form.html',ctx(request,title='Add a part / bin',form=form))


@require_POST
@access(warehouse=True)
def hold(request,pk):
    get_object_or_404(Stock,pk=pk)
    try: set_hold(request.user,pk,request.POST.get('held')=='1',request.POST.get('note',''))
    except WorkflowError as e: messages.error(request,str(e))
    else: messages.success(request,'Stock hold updated.')
    return redirect('stock_edit',pk=pk)


@require_POST
@access(warehouse=True)
def return_stock(request,pk):
    line=get_object_or_404(OrderLine,pk=pk)
    try:
        stock_id=int(request.POST.get('stock',''))
        get_object_or_404(Stock,pk=stock_id)
        receive_return(request.user,pk,stock_id,request.POST.get('amount',''),request.POST.get('note',''),uuid.UUID(request.POST.get('request_key','')),unit=request.POST.get('unit','BASE'))
    except (WorkflowError,ValueError) as e: messages.error(request,str(e))
    else: messages.success(request,'Return received into quarantine. Inspect before releasing.')
    return redirect('order_detail',pk=line.order_id)


def safe_cell(value):
    text=str(value if value is not None else '')
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else text


@access()
def inventory_export(request):
    response=HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="S3PL_FD_inventory.csv"'
    writer=csv.writer(response)
    writer.writerow(['SKU','Description','Base unit','Eaches per case','Cases per pallet','Location','Type','On hand','Reserved','Available','Needs count','On hold','Counted at'])
    for s in Stock.objects.select_related('part','location').order_by('part__sku','location__code'):
        writer.writerow([safe_cell(v) for v in [s.part.sku,s.part.description,s.part.unit,s.part.units_per_case,s.part.cases_per_pallet,s.location.code,s.kind,s.on_hand,s.reserved,s.available,s.count_required,s.held,s.last_counted_at]])
    return response


@access()
def packing_slip(request,pk):
    order=get_object_or_404(Order,pk=pk)
    return render(request,'packing_slip.html',ctx(request,order=order,shipments=order.shipments.prefetch_related('lines__line__part'),lines=order.lines.select_related('part')))
