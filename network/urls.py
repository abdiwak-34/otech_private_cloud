from django.urls import path
from . import views

# app_name lets us use {% url 'network:list' %} in templates
app_name = "network"

# IMPORTANT URL ordering rule:
# Django matches patterns from top to bottom.
# The path "routers/" must come BEFORE "<str:network_id>/"
# because the catch-all would otherwise grab the word "routers"
# and try to load it as a network ID — causing a 404.

urlpatterns = [

    # --- Network pages ---

    # List all networks and routers on one page
    path("", views.network_list, name="list"),

    # Create a new private network (name only — subnet is added on the next page)
    path("create/", views.create_network, name="create"),

    # Add a subnet to an existing network
    path("<str:network_id>/subnet/create/", views.create_subnet, name="create_subnet"),

    # --- Router pages (must be before the network catch-all) ---

    # List all routers
    path("routers/", views.router_list, name="router_list"),

    # Create a new router
    path("routers/create/", views.create_router, name="router_create"),

    # Router detail page — shows interfaces and gateway
    path("routers/<str:router_id>/", views.router_detail, name="router_detail"),

    # Delete a router
    path("routers/<str:router_id>/delete/", views.delete_router, name="router_delete"),

    # Add a subnet interface to a router
    path("routers/<str:router_id>/add-interface/", views.add_router_interface, name="router_add_interface"),

    # Remove a subnet interface from a router
    path("routers/<str:router_id>/remove-interface/", views.remove_router_interface, name="router_remove_interface"),

    # Update or clear the router's external gateway
    path("routers/<str:router_id>/set-gateway/", views.set_router_gateway, name="router_set_gateway"),

    # --- Network catch-all (must be last) ---

    # Network detail page
    path("<str:network_id>/", views.network_detail, name="detail"),

    # Delete a network
    path("<str:network_id>/delete/", views.delete_network, name="delete"),
]
