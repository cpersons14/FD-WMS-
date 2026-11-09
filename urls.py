from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from warehouse import views as v
urlpatterns=[path('admin/',admin.site.urls),path('login/',auth.LoginView.as_view(),name='login'),path('logout/',auth.LogoutView.as_view(),name='logout'),
 path('',v.dashboard,name='dashboard'),path('inventory/',v.inventory,name='inventory'),path('orders/',v.orders,name='orders'),
 path('orders/new/',v.new_order,name='new_order'),path('orders/<uuid:pk>/',v.order_detail,name='order_detail'),
 path('orders/<uuid:pk>/action/',v.order_action,name='order_action'),path('inventory/<int:pk>/',v.stock_edit,name='stock_edit'),
 path('inventory/<int:pk>/transfer/',v.transfer,name='transfer'),path('history/',v.history,name='history')]

from warehouse import extra_views as e
from warehouse import receiving_views as r
urlpatterns += [path('receipts/',r.receipts,name='receipts'),
 path('receipts/new/',r.receipt_edit,name='receipt_new'),
 path('receipts/<uuid:pk>/',r.receipt_detail,name='receipt_detail'),
 path('receipts/<uuid:pk>/edit/',r.receipt_edit,name='receipt_edit'),
 path('receipts/<uuid:pk>/email/',r.receipt_email_action,name='receipt_email_action'),
 path('receipt-documents/<uuid:pk>/',r.receipt_document,name='receipt_document')]
urlpatterns += [path('alerts/',e.alerts,name='alerts'),path('work/',e.work_queue,name='work_queue'),
 path('parts/<int:pk>/',e.part_settings,name='part_settings'),path('bins/new/',e.new_bin,name='new_bin'),
 path('inventory/<int:pk>/hold/',e.hold,name='hold'),path('returns/<int:pk>/',e.return_stock,name='return_stock'),
 path('inventory/export/',e.inventory_export,name='inventory_export'),path('orders/<uuid:pk>/print/',e.packing_slip,name='packing_slip')]
