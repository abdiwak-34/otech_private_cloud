from django.urls import path

from . import views

urlpatterns = [

    path('', views.home, name='home'),

    path('instances/', views.instances, name='instances'),

    path('images/', views.images, name='images'),

    # Cleanup — dedicated progress page (GET) + execute (POST)
    path(
        "instances/<str:instance_id>/cleanup/",
        views.cleanup_instance,
        name="cleanup",
    ),

    # AJAX status endpoint polled by the progress page
    path(
        "instances/<str:instance_id>/cleanup/status/",
        views.cleanup_status,
        name="cleanup_status",
    ),
]
