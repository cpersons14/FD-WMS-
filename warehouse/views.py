import uuid
import re
from urllib.parse import urlencode
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum, Count
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from .models import Part, Stock, Order, Event
from .forms import OrderForm, StockForm, TransferForm
from .services import (
    portal_user,
    warehouse_user,
    submit_order,
    transition_order,
    update_stock,
    transfer_stock,
    WorkflowError,
)


def access(warehouse=False):
    def decorate(fn):
        @login_required
        @wraps(fn)
        def wrapped(request, *args, **kwargs):
            if not (
                warehouse_user(request.user) if warehouse else portal_user(request.user)
            ):
                raise PermissionDenied
            return fn(request, *args, **kwargs)

        return wrapped

    return decorate


def ctx(request, **kwargs):
    return {"is_warehouse": warehouse_user(request.user), **kwargs}


@access()
def dashboard(request):
    return render(
        request,
        "dashboard.html",
        ctx(
            request,
            parts=Part.objects.count(),
            locations=Stock.objects.count(),
            uncounted=Stock.objects.filter(
                Q(on_hand__isnull=True) | Q(count_required=True)
            ).count(),
            open_orders=Order.objects.filter(
                status__in=["SUBMITTED", "PARTIAL", "PICKED", "PACKED"]
            ).count(),
            orders=Order.objects.all()[:6],
            low_stock=sum(
                r["low"]
                for r in __import__(
                    "warehouse.reporting", fromlist=["stock_summary"]
                ).stock_summary()
            ),
            recent_events=Event.objects.select_related(
                "actor", "stock__part", "stock__location"
            )[:8],
        ),
    )


@access()
def inventory(request):
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    counted = request.GET.get("counted", "")
    stocks = (
        Stock.objects.filter(archived=False)
        .select_related("part", "location")
        .order_by("part__sku", "kind", "location__code")
    )
    if q:
        matches = (
            Q(part__sku__icontains=q)
            | Q(part__description__icontains=q)
            | Q(location__code__icontains=q)
            | Q(part__barcode=q)
        )
        if warehouse_user(request.user):
            from .models import BackstockReference

            matches |= Q(
                part__sku__in=BackstockReference.objects.filter(
                    active=True, location__icontains=q
                ).values("sku")
            )
        stocks = stocks.filter(matches)
    if kind in ("PICK", "OVERSTOCK"):
        stocks = stocks.filter(kind=kind)
    if counted == "no":
        stocks = stocks.filter(Q(on_hand__isnull=True) | Q(count_required=True))
    from django.core.paginator import Paginator

    page = Paginator(stocks, 50).get_page(request.GET.get("page"))
    if warehouse_user(request.user):
        from .models import BackstockReference

        refs = (
            BackstockReference.objects.filter(
                active=True, sku__in=[s.part.sku for s in page]
            )
            .values("sku")
            .annotate(total=Count("id"))
        )
        counts = {r["sku"]: r["total"] for r in refs}
        for stock in page:
            stock.backstock_count = counts.get(stock.part.sku, 0)
    from .reporting import stock_summary

    part_ids = {s.part_id for s in page}
    summaries = [r for r in stock_summary() if r["part"].pk in part_ids]
    return render(
        request,
        "inventory.html",
        ctx(request, page=page, q=q, kind=kind, counted=counted, summaries=summaries),
    )


@access()
def orders(request):
    qs = Order.objects.all().order_by("-priority", "requested_ship_date", "created_at")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        match = Q(store__icontains=q)
        if re.fullmatch(r"FD-[0-9A-Fa-f]{8}", q, re.IGNORECASE):
            match |= Q(id__istartswith=q[3:])
        qs = qs.filter(
            match
            | Q(reference__icontains=q)
            | Q(tracking__icontains=q)
            | Q(shipments__tracking__icontains=q)
            | Q(document_number__icontains=q)
            | Q(requisitions__reference__icontains=" ".join(q.split()))
        )
    qs = qs.distinct()
    if status in dict(Order._meta.get_field("status").choices):
        qs = qs.filter(status=status)
    from django.core.paginator import Paginator

    return render(
        request,
        "orders.html",
        ctx(
            request,
            page=Paginator(qs, 40).get_page(request.GET.get("page")),
            q=q,
            status=status,
        ),
    )


@access()
def new_order(request):
    form = OrderForm(request.POST or None)
    error = ""
    selected = []
    from django import forms

    form.fields["allow_backorder"].widget = forms.HiddenInput()
    if not warehouse_user(request.user):
        form.fields["priority"].widget = forms.HiddenInput()
    key = request.POST.get("request_key", str(uuid.uuid4()))
    if request.method == "POST":
        selected = [
            (s, q, u)
            for s, q, u in zip(
                request.POST.getlist("sku"),
                request.POST.getlist("quantity"),
                request.POST.getlist("unit")
                or ["EA"] * len(request.POST.getlist("sku")),
            )
        ]
    if request.method == "POST" and form.is_valid():
        try:
            request_key = uuid.UUID(key)
            skus = request.POST.getlist("sku")
            quantities = request.POST.getlist("quantity")
            units = request.POST.getlist("unit") or ["EA"] * len(skus)
            if len(skus) != len(quantities) or len(skus) != len(units):
                raise WorkflowError("Order lines are incomplete.")
            if any(u != "EA" for u in units):
                raise WorkflowError("Order quantities must be in EA only.")
            form.cleaned_data["allow_backorder"] = True
            selected = [
                (s.strip(), q.strip(), u.strip())
                for s, q, u in zip(skus, quantities, units)
                if s.strip() or q.strip()
            ]
            order = submit_order(request.user, form.cleaned_data, selected, request_key)
            messages.success(
                request,
                "Order submitted. Available stock is reserved; any shortage is shown as backordered.",
            )
            return redirect("order_detail", pk=order.pk)
        except (WorkflowError, ValueError) as e:
            error = str(e)
    catalog = Part.objects.annotate(
        total=Sum("stock__on_hand"),
        held=Sum("stock__reserved"),
        unknown=Count("stock", filter=Q(stock__on_hand__isnull=True)),
    ).order_by("sku")
    return render(
        request,
        "new_order.html",
        ctx(
            request,
            form=form,
            error=error,
            catalog=catalog,
            request_key=key,
            selected=selected,
        ),
    )


@access()
def order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk)
    requisitions = list(
        order.requisitions.select_related("shipment").prefetch_related(
            "lines__line__part"
        )
    )
    selected_id = request.GET.get("requisition", "")
    selected = next((r for r in requisitions if str(r.pk) == selected_id), None)
    if selected is None and requisitions:
        selected = next(
            (r for r in requisitions if r.status not in {"Shipped", "Cancelled"}),
            requisitions[-1],
        )
    return render(
        request,
        "order_detail.html",
        ctx(
            request,
            order=order,
            lines=order.lines.select_related("part").prefetch_related(
                "allocations__stock__location"
            ),
            events=order.event_set.select_related("actor"),
            request_key=str(uuid.uuid4()),
            return_bins=Stock.objects.filter(
                held=True, count_required=False, on_hand__isnull=False
            ).select_related("part", "location"),
            shipments=order.shipments.select_related("requisition").order_by(
                "created_at"
            ),
            requisitions=requisitions,
            selected=selected,
            release_lines=selected.lines.select_related("line__part")
            if selected
            else [],
            release_allocations=selected.allocations.select_related(
                "stock__location", "line__part"
            )
            if selected
            else [],
        ),
    )


@require_POST
@access(warehouse=True)
def order_action(request, pk):
    get_object_or_404(Order, pk=pk)
    try:
        line_id = int(request.POST["line"]) if request.POST.get("line") else None
        transition_order(
            request.user,
            pk,
            request.POST.get("action"),
            request.POST.get("carrier", ""),
            request.POST.get("tracking", ""),
            line_id=line_id,
            amount=request.POST.get("amount"),
            scan=request.POST.get("scan", ""),
            key=uuid.UUID(request.POST.get("request_key", "")),
            note=request.POST.get("note", ""),
            unit=request.POST.get("unit", "BASE"),
            requisition_id=request.POST.get("requisition") or None,
        )
    except (WorkflowError, ValueError) as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Order updated.")
    url = reverse("order_detail", kwargs={"pk": pk})
    if request.POST.get("requisition"):
        url += "?" + urlencode({"requisition": request.POST["requisition"]})
    return redirect(url)


@access(warehouse=True)
def stock_edit(request, pk):
    stock = get_object_or_404(
        Stock.objects.filter(archived=False).select_related("part", "location"), pk=pk
    )
    form = StockForm(request.POST or None, initial={"key": uuid.uuid4()})
    if request.method == "POST" and form.is_valid():
        try:
            update_stock(request.user, pk, **form.cleaned_data)
        except WorkflowError as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, "Inventory updated and recorded in history.")
            return redirect("inventory")
    return render(request, "stock_edit.html", ctx(request, stock=stock, form=form))


@access(warehouse=True)
def transfer(request, pk):
    stock = get_object_or_404(
        Stock.objects.filter(archived=False).select_related("part", "location"), pk=pk
    )
    form = TransferForm(
        request.POST or None, stock=stock, initial={"key": uuid.uuid4()}
    )
    if request.method == "POST" and form.is_valid():
        try:
            transfer_stock(
                request.user,
                pk,
                form.cleaned_data["target"].pk,
                form.cleaned_data["amount"],
                form.cleaned_data["note"],
                key=form.cleaned_data["key"],
                unit=form.cleaned_data["unit"],
                loose_eaches=form.cleaned_data["loose_eaches"],
            )
        except WorkflowError as e:
            form.add_error(None, str(e))
        else:
            messages.success(
                request, "Transfer complete. Related reservations moved with the stock."
            )
            return redirect("inventory")
    return render(
        request,
        "stock_edit.html",
        ctx(request, stock=stock, form=form, is_transfer=True),
    )


@access(warehouse=True)
def history(request):
    from django.core.paginator import Paginator

    events = Event.objects.select_related(
        "actor", "stock__part", "stock__location", "order", "receipt"
    )
    return render(
        request,
        "history.html",
        ctx(request, page=Paginator(events, 60).get_page(request.GET.get("page"))),
    )
