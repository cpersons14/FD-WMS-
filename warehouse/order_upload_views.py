import csv
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from .views import access, ctx
from .update_views import ReportForm
from .models import OrderUpload, Order, Part
from .services import WorkflowError
from .order_upload import HEADERS, preview_upload, submit_upload


@access()
def upload(request):
    form = ReportForm(request.POST or None, request.FILES or None)
    form.fields["report"].label = "Order file (XLSX or CSV)"
    if request.method == "POST" and form.is_valid():
        try:
            file = form.cleaned_data["report"]
            batch = preview_upload(request.user, file.read(), file.name)
            return redirect("order_upload_preview", pk=batch.pk)
        except WorkflowError as exc:
            form.add_error(None, str(exc))
    return render(
        request,
        "order_upload.html",
        ctx(
            request,
            form=form,
            order_rules=Part.objects.filter(order_multiple__gt=1).order_by("sku"),
        ),
    )


@access()
def preview(request, pk):
    batch = get_object_or_404(OrderUpload, pk=pk, actor=request.user)
    if request.method == "POST":
        try:
            batch = submit_upload(request.user, batch.pk)
        except WorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"{len(batch.order_ids)} orders submitted. Repeating this confirmation cannot create duplicates.",
            )
        return redirect("order_upload_preview", pk=pk)
    return render(
        request,
        "order_upload_preview.html",
        ctx(request, batch=batch, orders=Order.objects.filter(pk__in=batch.order_ids)),
    )


@access()
def template(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="FD_order_upload_template.csv"'
    )
    csv.writer(response).writerow(HEADERS)
    return response
