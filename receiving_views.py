import uuid
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST
from .models import InboundReceipt,ReceiptDocument,ReceiptGroup,Part,Stock
from .views import access,ctx
from .services import WorkflowError
from .receiving import save_receipt,post_receipt,read_uploads,send_receipt_email


class ReceiptForm(forms.ModelForm):
    notes=forms.CharField(max_length=5000,required=False,label='Shortages, damage and delivery notes',widget=forms.Textarea(attrs={'rows':3}))
    class Meta:
        model=InboundReceipt
        fields=['supplier','received_date','reference','group','notes']
        labels={'reference':'BOL / packing list / delivery reference','group':'Send to','notes':'Shortages, damage and delivery notes'}
        widgets={'received_date':forms.DateInput(attrs={'type':'date'}),'notes':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['group'].queryset=ReceiptGroup.objects.filter(active=True)


@access(warehouse=True)
def receipts(request):
    q=request.GET.get('q','').strip();status=request.GET.get('status','')
    qs=InboundReceipt.objects.select_related('email','group','created_by')
    if q:
        qs=qs.filter(Q(supplier__icontains=q)|Q(reference__icontains=q)|Q(id__icontains=q.replace('RCV-','').replace('-',''))|Q(lines__sku__icontains=q)).distinct()
    if status in ('DRAFT','CLARIFY','POSTED'): qs=qs.filter(status=status)
    return render(request,'receipts.html',ctx(request,page=Paginator(qs,40).get_page(request.GET.get('page')),q=q,status=status))


def parse_rows(data):
    fields=['sku','location','quantity','unit','loose_eaches','case_pack','pallet_cases','note','damaged']
    arrays={f:data.getlist(f) for f in fields}
    size=len(arrays['sku'])
    if size>100 or any(len(v)!=size for v in arrays.values()): raise WorkflowError('Receipt rows are incomplete. Reload and try again.')
    rows=[]
    for i in range(size):
        row={f:arrays[f][i].strip() for f in fields}
        if any(len(row[f])>(500 if f=='note' else 100) for f in fields): raise WorkflowError('A receipt row contains text that is too long.')
        if not any(row[f] for f in ['sku','location','quantity','note']): continue
        row['damaged']=row['damaged']=='1'
        if row['unit'] not in ('EA','CASE','PALLET'): raise WorkflowError('Select eaches, cases or pallets.')
        rows.append(row)
    return rows


@access(warehouse=True)
def receipt_edit(request,pk=None):
    receipt=get_object_or_404(InboundReceipt,pk=pk) if pk else None
    if receipt and receipt.status=='POSTED': return redirect('receipt_detail',pk=pk)
    initial={'received_date':timezone.localdate(),'group':ReceiptGroup.objects.filter(active=True,name='FD Receiving').first()}
    form=ReceiptForm(request.POST if request.method=='POST' else None,instance=receipt,initial=initial if not receipt else None)
    rows=receipt.draft_lines if receipt else []
    request_key=request.POST.get('request_key',str(receipt.request_key if receipt else uuid.uuid4()))
    error=''
    if request.method=='POST':
        try:
            rows=parse_rows(request.POST)
            if form.is_valid():
                action=request.POST.get('action','draft')
                if action not in ('draft','clarify','submit'): raise WorkflowError('Choose Save Draft or Submit Receipt.')
                files=read_uploads(request.FILES.getlist('documents'))
                saved=save_receipt(request.user,uuid.UUID(request_key),pk,int(request.POST.get('revision','0')),form.cleaned_data,rows,files,
                    request.POST.getlist('keep_docs'),request.POST.getlist('email_docs'),request.POST.getlist('new_email'),status='CLARIFY' if action=='clarify' else 'DRAFT')
                if action=='submit':
                    try: posted=post_receipt(request.user,saved.pk,saved.revision)
                    except WorkflowError as exc:
                        messages.error(request,f'Draft saved; inventory has not posted. {exc}')
                        return redirect('receipt_edit',pk=saved.pk)
                    result=send_receipt_email(posted.pk,actor=request.user)
                    messages.success(request,f'Inventory posted once. Customer email: {result.lower().replace("_"," ")}.')
                    return redirect('receipt_detail',pk=posted.pk)
                messages.success(request,'Receipt draft saved. Inventory has not changed.')
                return redirect('receipt_edit',pk=saved.pk)
        except (WorkflowError,ValueError) as exc:
            error=str(exc)
        if request.FILES: messages.warning(request,'This save did not complete. Select the new upload files again before retrying.')
    catalog=[dict(sku=p.sku,description=p.description,unit=p.unit,case_pack=p.units_per_case,pallet_cases=p.cases_per_pallet) for p in Part.objects.order_by('sku')]
    bins=[dict(sku=s.part.sku,location=s.location.code,held=s.held,uncounted=s.count_required or s.on_hand is None) for s in Stock.objects.select_related('part','location')]
    return render(request,'receipt_edit.html',ctx(request,form=form,receipt=receipt,rows=rows or [{}],catalog=catalog,bins=bins,request_key=request_key,error=error,documents=receipt.documents.defer('content') if receipt else [],email_enabled=settings.WMS_RECEIPT_EMAIL_ENABLED))


@access(warehouse=True)
def receipt_detail(request,pk):
    receipt=get_object_or_404(InboundReceipt.objects.select_related('posted_by','email'),pk=pk)
    if receipt.status!='POSTED': return redirect('receipt_edit',pk=pk)
    return render(request,'receipt_detail.html',ctx(request,receipt=receipt,lines=receipt.lines.all(),documents=receipt.documents.defer('content'),email=receipt.email,email_enabled=settings.WMS_RECEIPT_EMAIL_ENABLED))


@access(warehouse=True)
def receipt_document(request,pk):
    doc=get_object_or_404(ReceiptDocument,pk=pk)
    response=HttpResponse(bytes(doc.content),content_type=doc.mime_type)
    response['Content-Disposition']=content_disposition_header(request.GET.get('download')=='1',doc.filename)
    response['Cache-Control']='private, no-store'
    response['Content-Security-Policy']="sandbox; default-src 'none'"
    response['X-Content-Type-Options']='nosniff'
    return response


@require_POST
@access(warehouse=True)
def receipt_email_action(request,pk):
    receipt=get_object_or_404(InboundReceipt,pk=pk,status='POSTED')
    try:
        result=send_receipt_email(receipt.pk,actor=request.user,retry=True,checked_delivery=request.POST.get('checked_delivery')=='on')
        messages.info(request,f'Customer email: {result.lower().replace("_"," ")}. Inventory was not changed.')
    except WorkflowError as exc: messages.error(request,str(exc))
    return redirect('receipt_detail',pk=pk)
