from django.contrib import admin
from .models import StoreLocation
from .models import Part, Location, Stock, Order, OrderLine, Allocation, Event
from .models import DocumentSequence, Requisition, RequisitionLine

admin.site.site_header = "S3PL Customer Operations"
admin.site.site_title = "S3PL WMS"


class ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StoreLocation)
class StoreLocationAdmin(ReadOnly):
    list_display = [
        "store_number",
        "address",
        "city",
        "state",
        "postal_code",
        "county",
        "location_type",
        "active",
    ]
    search_fields = ["store_number", "postal_code", "city", "address", "county"]
    list_filter = ["customer_code", "state", "active"]


@admin.register(Stock)
class StockAdmin(ReadOnly):
    list_display = ["part", "location", "kind", "on_hand", "reserved", "count_required"]
    search_fields = ["part__sku", "location__code"]
    list_filter = ["kind"]


@admin.register(Event)
class EventAdmin(ReadOnly):
    list_display = ["at", "actor", "action", "stock", "quantity", "before", "after"]
    search_fields = [
        "action",
        "note",
        "receipt__supplier",
        "receipt__po_number",
        "receipt__reference",
        "stock__part__sku",
        "actor__username",
    ]
    list_filter = ["action"]


admin.site.register(Order, ReadOnly)
admin.site.register(OrderLine, ReadOnly)
admin.site.register(Allocation, ReadOnly)
admin.site.register(DocumentSequence, ReadOnly)
admin.site.register(Requisition, ReadOnly)
admin.site.register(RequisitionLine, ReadOnly)

admin.site.register(Part, ReadOnly)
admin.site.register(Location, ReadOnly)
from .models import (
    Shipment,
    ShipmentLine,
    ReturnReceipt,
    AlertSubscription,
    AlertDelivery,
    OperationKey,
)

for model in [
    Shipment,
    ShipmentLine,
    ReturnReceipt,
    AlertSubscription,
    AlertDelivery,
    OperationKey,
]:
    admin.site.register(model, ReadOnly)

from .models import (
    ReceiptGroup,
    InboundReceipt,
    InboundReceiptLine,
    ReceiptDocument,
    ReceiptEmail,
    ReceiptEmailAttempt,
)


@admin.register(ReceiptGroup)
class ReceiptGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "active"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        Event.objects.create(
            actor=request.user,
            action="RECEIPT_GROUP_SETTINGS",
            note=f"Group {obj.pk} ({obj.name}) configuration updated.",
        )

    def has_delete_permission(self, request, obj=None):
        return False


for model in [InboundReceipt, InboundReceiptLine, ReceiptEmail, ReceiptEmailAttempt]:
    admin.site.register(model, ReadOnly)


@admin.register(ReceiptDocument)
class ReceiptDocumentAdmin(ReadOnly):
    exclude = ["content"]
    list_display = ["filename", "receipt", "size", "uploaded_at"]


from .models import PickRelease, PurchaseOrder, PurchaseOrderLine

for model in [PickRelease, PurchaseOrder, PurchaseOrderLine]:
    admin.site.register(model, ReadOnly)

from .models import BackstockReference, BackstockImport


@admin.register(BackstockReference)
class BackstockReferenceAdmin(ReadOnly):
    list_display = ["sku", "location", "active", "updated_at", "updated_by"]
    search_fields = ["sku", "description", "location"]
    list_filter = ["active"]


admin.site.register(BackstockImport, ReadOnly)
