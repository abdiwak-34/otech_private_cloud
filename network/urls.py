from django.urls import path
from . import views

app_name = "network"

urlpatterns = [

    # ---- Networks ----
    path("",               views.network_list,   name="list"),
    path("create/",        views.create_network, name="create"),
    path("<str:network_id>/",        views.network_detail, name="detail"),
    path("<str:network_id>/delete/", views.delete_network, name="delete"),

    # ---- Routers ----
    path("routers/",                         views.router_list,   name="router_list"),
    path("routers/create/",                  views.create_router, name="router_create"),
    path("routers/<str:router_id>/",         views.router_detail, name="router_detail"),
    path("routers/<str:router_id>/delete/",  views.delete_router, name="router_delete"),

    # ---- Router interfaces ----
    path(
        "routers/<str:router_id>/add-interface/",
        views.add_router_interface,
        name="router_add_interface",
    ),
    path(
        "routers/<str:router_id>/remove-interface/",
        views.remove_router_interface,
        name="router_remove_interface",
    ),
    path(
        "routers/<str:router_id>/set-gateway/",
        views.set_router_gateway,
        name="router_set_gateway",
    ),
]
