from django.urls import path
from . import views

app_name = "instance"

urlpatterns = [
    path("new/", views.create_instance, name="create"),
    path(
        "<str:instance_id>/delete/",
        views.delete_instance,
        name="delete"
    ),
    path(
        "<str:instance_id>/",
        views.instance_detail,
        name="detail"
    ),

    path(
        "<str:instance_id>/floating-ip/",
        views.assign_floating_ip,
        name="assign_floating_ip"
    ),

    path(
        "<str:instance_id>/volume/create/",
        views.create_volume,
        name="create_volume"
    ),

    path(
        "<str:instance_id>/volume/<str:volume_id>/attach/",
        views.attach_volume,
        name="attach_volume"
    ),

    path(
        "<str:instance_id>/volume/<str:volume_id>/detach/",
        views.detach_volume,
        name="detach_volume"
    ),

    path(
        "<str:instance_id>/volume/<str:volume_id>/delete/",
        views.delete_volume,
        name="delete_volume"
    ),
]
