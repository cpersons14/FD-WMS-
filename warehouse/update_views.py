import csv
from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .views import access, ctx
from .models import (
    PickRelease,
    Order,
    PurchaseOrder,
    PurchaseOrderLine,
    InboundReceipt,
    InboundReceiptLine,
)
from .services import WorkflowError
from .backorders import change_priority
from .purchase_orders import (
    HEADERS,
    import_purchase_orders,
    progress,
    correct_expected,
    correct_accepted,
    correct_receipt_po,
)


@access(warehouse=True)
def release_detail(request, pk):
    release = get_object_or_404(
        PickRelease.objects.select_related("order", "receipt"), pk=pk
    )
    return render(
        request,
        "pick_release.html",
        ctx(
            request,
            release=release,
            allocations=release.allocations.select_related(
                "line__part", "stock__location"
            ).order_by("stock__location__code", "line__part__sku"),
        ),
    )


@require_POST
@access(warehouse=True)
def priority(request, pk):
    get_object_or_404(Order, pk=pk)
    try:
        change_priority(
            request.user, pk, request.POST.get("priority"), request.POST.get("note", "")
        )
    except WorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Priority saved. Urgent orders take priority on the next release; existing reservations stay assigned.",
        )
    return redirect("order_detail", pk=pk)


class ReportForm(forms.Form):
    report = forms.FileField(label="PO report (XLSX or CSV)")

    def clean_report(self):
        file = self.cleaned_data["report"]
        if file.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Use a file under 10 MB.")
        return file


@access(warehouse=True)
def purchase_orders(request):
    form = ReportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            file = form.cleaned_data["report"]
            result = import_purchase_orders(request.user, file.read(), file.name)
            messages.success(
                request,
                "This report was already imported."
                if result["unchanged"]
                else f"Reviewed {result['orders']} POs; added {result['lines']} expected item lines.",
            )
            return redirect("purchase_orders")
        except WorkflowError as exc:
            form.add_error(None, str(exc))
    q = request.GET.get("q", "").strip()
    qs = PurchaseOrder.objects.filter(
        Q(number__icontains=q) | Q(vendor__icontains=q)
    ).order_by("-created_at")
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(
        request,
        "purchase_orders.html",
        ctx(request, form=form, page=page, rows=[progress(po) for po in page], q=q),
    )


@access(warehouse=True)
def po_template(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="FD_PO_import_template.csv"'
    csv.writer(response).writerow(HEADERS)
    return response


@access(warehouse=True)
def po_detail(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method == "POST":
        line = get_object_or_404(
            PurchaseOrderLine, pk=request.POST.get("line"), purchase_order=po
        )
        try:
            correct_expected(
                request.user,
                line.pk,
                request.POST.get("expected"),
                request.POST.get("note", ""),
            )
        except WorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request, "Expected quantity corrected and recorded in history."
            )
        return redirect("po_detail", pk=pk)
    from .models import InboundReceipt

    return render(
        request,
        "po_detail.html",
        ctx(
            request,
            **progress(po),
            receipts=InboundReceipt.objects.filter(
                Q(po_number=po.number) | Q(lines__po_number=po.number)
            ).distinct(),
        ),
    )


@require_POST
@access(warehouse=True)
def accepted(request, pk):
    line = get_object_or_404(InboundReceiptLine, pk=pk)
    try:
        correct_accepted(
            request.user,
            pk,
            request.POST.get("accepted"),
            request.POST.get("note", ""),
            request.POST.get("po_number"),
        )
    except WorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "PO accepted quantity updated with an audit record. Stock quantities and the original receipt email were not changed.",
        )
    return redirect("receipt_detail", pk=line.receipt_id)


@require_POST
@access(warehouse=True)
def receipt_po(request, pk):
    get_object_or_404(InboundReceipt, pk=pk, status="POSTED")
    try:
        correct_receipt_po(
            request.user,
            pk,
            request.POST.get("po_number", ""),
            request.POST.get("note", ""),
        )
    except WorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Receipt PO reference corrected with an audit record. The original customer email remains unchanged.",
        )
    return redirect("receipt_detail", pk=pk)
