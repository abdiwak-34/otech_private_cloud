from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('instances/', views.instances, name='instances'),
    path('images/', views.images, name='images'),
    path('networks/', views.networks, name='networks'),
]
