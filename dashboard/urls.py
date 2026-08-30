from django.urls import path
from . import views

# These URL patterns belong to the root of the project (mounted at /)
# Each path maps a URL to a view function defined in views.py

urlpatterns = [

    # Home dashboard — shows a summary of all cloud resources
    path("", views.home, name="home"),

    # Instances list page — shows all running VMs
    path("instances/", views.instances, name="instances"),

    # Images list page — shows all Glance images
    path("images/", views.images, name="images"),

    # Cleanup page — GET shows a confirmation page,
    # POST runs the actual cleanup and streams progress back
    path("instances/<str:instance_id>/cleanup/", views.cleanup_instance, name="cleanup"),

    # Status check endpoint — the cleanup page calls this to check
    # whether the instance still exists during deletion
    path("instances/<str:instance_id>/cleanup/status/", views.cleanup_status, name="cleanup_status"),
]
