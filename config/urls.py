from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from warehouse.health import ready
from warehouse import views as v
from warehouse import store_views as stores
from warehouse import update_views as update
from warehouse import order_upload_views as upload
from warehouse import backstock_views as backstock

urlpatterns = [
    path("health/ready/", ready, name="health_ready"),
    path("admin/", admin.site.urls),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("stores/search/", stores.store_search, name="store_search"),
    path("stores/import/", stores.directory_import, name="directory_import"),
    path("", v.dashboard, name="dashboard"),
    path("inventory/", v.inventory, name="inventory"),
    path("orders/", v.orders, name="orders"),
    path("orders/new/", v.new_order, name="new_order"),
    path("orders/<uuid:pk>/", v.order_detail, name="order_detail"),
    path("orders/<uuid:pk>/action/", v.order_action, name="order_action"),
    path("inventory/<int:pk>/", v.stock_edit, name="stock_edit"),
    path("inventory/<int:pk>/transfer/", v.transfer, name="transfer"),
    path("history/", v.history, name="history"),
]

from warehouse import extra_views as e
from warehouse import receiving_views as r

urlpatterns += [
    path("receipts/", r.receipts, name="receipts"),
    path("receipts/new/", r.receipt_edit, name="receipt_new"),
    path("receipts/<uuid:pk>/", r.receipt_detail, name="receipt_detail"),
    path("receipts/<uuid:pk>/edit/", r.receipt_edit, name="receipt_edit"),
    path(
        "receipts/<uuid:pk>/email/", r.receipt_email_action, name="receipt_email_action"
    ),
    path("receipt-documents/<uuid:pk>/", r.receipt_document, name="receipt_document"),
]
urlpatterns += [
    path("alerts/", e.alerts, name="alerts"),
    path("work/", e.work_queue, name="work_queue"),
    path("parts/<int:pk>/", e.part_settings, name="part_settings"),
    path("bins/new/", e.new_bin, name="new_bin"),
    path("inventory/<int:pk>/hold/", e.hold, name="hold"),
    path("returns/<int:pk>/", e.return_stock, name="return_stock"),
    path("inventory/export/", e.inventory_export, name="inventory_export"),
    path("orders/<uuid:pk>/print/", e.packing_slip, name="packing_slip"),
]
urlpatterns += [
    path("releases/<uuid:pk>/", update.release_detail, name="release_detail"),
    path("orders/<uuid:pk>/priority/", update.priority, name="order_priority"),
    path("purchase-orders/", update.purchase_orders, name="purchase_orders"),
    path("purchase-orders/template/", update.po_template, name="po_template"),
    path("purchase-orders/<int:pk>/", update.po_detail, name="po_detail"),
    path("receipt-lines/<int:pk>/accepted/", update.accepted, name="receipt_accepted"),
]
urlpatterns += [path("receipts/<uuid:pk>/po/", update.receipt_po, name="receipt_po")]
urlpatterns += [
    path("orders/upload/", upload.upload, name="order_upload"),
    path("orders/upload/template/", upload.template, name="order_upload_template"),
    path("orders/upload/<uuid:pk>/", upload.preview, name="order_upload_preview"),
]
urlpatterns += [
    path("backstock/", backstock.references, name="backstock"),
    path("backstock/new/", backstock.edit, name="backstock_new"),
    path("backstock/import/", backstock.upload, name="backstock_upload"),
    path("backstock/<int:pk>/", backstock.edit, name="backstock_edit"),
]
