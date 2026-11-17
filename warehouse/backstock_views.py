from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from .models import BackstockReference, BackstockImport, Part, Event
from .views import access, ctx
from .services import WorkflowError
from .backstock import read_locations, import_locations


class ReferenceForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput, required=False)

    class Meta:
        model = BackstockReference
        fields = ["sku", "description", "location", "active", "notes"]
        labels = {
            "sku": "Part number",
            "location": "Backstock location",
            "active": "Show as an active reference",
        }
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class ReferenceUploadForm(forms.Form):
    report = forms.FileField(
        label="Inventory workbook (.xlsx)",
        widget=forms.FileInput(attrs={"accept": ".xlsx"}),
    )

    def clean_report(self):
        f = self.cleaned_data["report"]
        if f.size > 8 * 1024 * 1024:
            raise forms.ValidationError("Use a workbook no larger than 8 MB.")
        return f


@access(warehouse=True)
def references(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "active")
    qs = BackstockReference.objects.select_related("source_import", "updated_by")
    if status != "all":
        qs = qs.filter(active=status != "inactive")
    if q:
        qs = qs.filter(
            Q(sku__icontains=q) | Q(description__icontains=q) | Q(location__icontains=q)
        )
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    known = {p.sku: p.pk for p in Part.objects.filter(sku__in=[r.sku for r in page])}
    for row in page:
        row.catalog_id = known.get(row.sku)
    active = BackstockReference.objects.filter(active=True)
    return render(
        request,
        "backstock.html",
        ctx(
            request,
            page=page,
            q=q,
            status=status,
            total=active.count(),
            part_count=active.values("sku").distinct().count(),
            latest=BackstockImport.objects.order_by("-created_at").first(),
        ),
    )


@access(warehouse=True)
def edit(request, pk=None):
    reference = get_object_or_404(BackstockReference, pk=pk) if pk else None
    initial = (
        {"revision": reference.revision}
        if reference
        else {"sku": request.GET.get("sku", ""), "active": True, "revision": 0}
    )
    form = ReferenceForm(instance=reference, initial=initial)
    if request.method == "POST":
        try:
            with transaction.atomic():
                reference = (
                    get_object_or_404(
                        BackstockReference.objects.select_for_update(), pk=pk
                    )
                    if pk
                    else None
                )
                before = (
                    {k: getattr(reference, k) for k in ReferenceForm.Meta.fields}
                    if reference
                    else {}
                )
                form = ReferenceForm(request.POST, instance=reference)
                if form.is_valid():
                    if (
                        reference
                        and form.cleaned_data["revision"] != reference.revision
                    ):
                        form.add_error(
                            None,
                            "This reference changed while you were editing. Reload the page before saving.",
                        )
                    else:
                        row = form.save(commit=False)
                        row.updated_by = request.user
                        row.revision = (reference.revision + 1) if reference else 0
                        row.save()
                        Event.objects.create(
                            actor=request.user,
                            action="BACKSTOCK_REFERENCE_EDITED"
                            if pk
                            else "BACKSTOCK_REFERENCE_ADDED",
                            entry={
                                "reference_id": row.pk,
                                "sku": row.sku,
                                "location": row.location,
                                "previous": before,
                                "current": {
                                    k: getattr(row, k)
                                    for k in ReferenceForm.Meta.fields
                                },
                            },
                            note=f"{row.sku} / {row.location}: reference updated; {'active' if row.active else 'inactive'}. No quantity changed.",
                        )
                        messages.success(
                            request,
                            "Location reference saved. Inventory quantities were not changed.",
                        )
                        return redirect("backstock")
        except IntegrityError:
            form.add_error(
                None,
                "That part and location already have a reference. Edit the existing record.",
            )
    return render(
        request, "backstock_edit.html", ctx(request, form=form, reference=reference)
    )


@access(warehouse=True)
def upload(request):
    form = ReferenceUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            f = form.cleaned_data["report"]
            payload = read_locations(f.read(), f.name)
            batch, created = import_locations(request.user, payload)
        except WorkflowError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(
                request,
                f"{batch.added_count} backstock references added; {batch.existing_count} existing references preserved. No quantities imported."
                if created
                else "This workbook was already imported. Later edits and inactive references were preserved.",
            )
            return redirect("backstock")
    return render(request, "backstock_upload.html", ctx(request, form=form))
