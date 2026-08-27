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
]
