from django.urls import path
from . import views

# The app_name creates a namespace so we can use {% url 'instance:detail' %}
# in templates without clashing with other apps.
app_name = "instance"

# IMPORTANT: Django matches URLs top to bottom.
# The catch-all path("<str:instance_id>/") must come LAST,
# otherwise it would swallow all the more specific paths below it.

urlpatterns = [

    # Create a new instance — form page
    path("new/", views.create_instance, name="create"),

    # Delete an instance — shows a confirmation page first
    path("<str:instance_id>/delete/", views.delete_instance, name="delete"),

    # Assign a public floating IP to an instance
    path("<str:instance_id>/floating-ip/", views.assign_floating_ip, name="assign_floating_ip"),

    # Create a new Cinder volume (optionally auto-attach it)
    path("<str:instance_id>/volume/create/", views.create_volume, name="create_volume"),

    # Attach an existing volume to this instance
    path("<str:instance_id>/volume/<str:volume_id>/attach/", views.attach_volume, name="attach_volume"),

    # Detach a volume from this instance
    path("<str:instance_id>/volume/<str:volume_id>/detach/", views.detach_volume, name="detach_volume"),

    # Delete a volume permanently
    path("<str:instance_id>/volume/<str:volume_id>/delete/", views.delete_volume, name="delete_volume"),

    # Instance detail page — must be last because it matches any UUID
    path("<str:instance_id>/", views.instance_detail, name="detail"),
]
