from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Case, When, Value, IntegerField
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import StoreLocation
from .store_directory import import_report, MAX_BYTES
from .views import access, ctx


@never_cache
@require_GET
@access()
def store_search(request):
    query = request.GET.get("q", "").strip()[:100]
    if len(query) < 2 and not query.isdigit():
        return JsonResponse({"results": [], "more": False})
    stores = StoreLocation.objects.filter(customer_code="FD", active=True)
    for word in query.split():
        term = word.lstrip("#")
        match = (
            Q(store_number__icontains=term)
            | Q(postal_code__icontains=term)
            | Q(city__icontains=term)
            | Q(state__iexact=term)
            | Q(address__icontains=term)
            | Q(county__icontains=term)
        )
        if term.isdigit():
            match |= Q(store_number=term.lstrip("0") or "0")
        stores = stores.filter(match)
    stores = stores.annotate(
        rank=Case(
            When(store_number=query.lstrip("#"), then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("rank", "store_number")
    results = list(stores[:21])
    return JsonResponse(
        {
            "results": [dict(id=s.pk, **s.order_details()) for s in results[:20]],
            "more": len(results) > 20,
        }
    )


class DirectoryUpload(forms.Form):
    report = forms.FileField(
        label="FD Active Location Report (.xlsx)",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
    )

    def clean_report(self):
        upload = self.cleaned_data["report"]
        if not upload.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Choose the Excel .xlsx report.")
        if upload.size > MAX_BYTES:
            raise forms.ValidationError("Choose a report smaller than 10 MB.")
        return upload


@access(warehouse=True)
def directory_import(request):
    form = DirectoryUpload(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        report = form.cleaned_data["report"]
        try:
            counts = import_report(request.user, report.read(), report.name)
        except ValidationError as exc:
            form.add_error("report", exc)
        else:
            text = (
                f"This report was already imported ({counts['total']:,} stores)."
                if counts["repeated"]
                else f"Store directory updated: {counts['created']:,} added, {counts['updated']:,} updated, {counts['unchanged']:,} unchanged."
            )
            messages.success(request, text)
            return redirect("directory_import")
    return render(
        request,
        "store_directory_import.html",
        ctx(
            request,
            form=form,
            store_count=StoreLocation.objects.filter(
                customer_code="FD", active=True
            ).count(),
        ),
    )
