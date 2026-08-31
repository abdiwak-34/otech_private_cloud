from django.urls import path
from . import views

app_name = "instance"

# IMPORTANT: Django matches URLs top to bottom.
# path("<str:instance_id>/") is a catch-all — it must come LAST.
# Every specific path must be listed before it.

urlpatterns = [

    # Create a new instance
    path("new/", views.create_instance, name="create"),

    # --- Security group management (must be before <str:instance_id>/) ---
    path("security-groups/",                                             views.security_group_list,        name="security_group_list"),
    path("security-groups/create/",                                      views.create_security_group,      name="security_group_create"),
    path("security-groups/<str:sg_id>/",                                 views.security_group_detail,      name="security_group_detail"),
    path("security-groups/<str:sg_id>/delete/",                          views.delete_security_group,      name="security_group_delete"),
    path("security-groups/<str:sg_id>/rules/add/",                       views.add_security_group_rule,    name="sg_rule_add"),
    path("security-groups/<str:sg_id>/rules/<str:rule_id>/delete/",      views.delete_security_group_rule, name="sg_rule_delete"),

    # --- Instance sub-resource paths (must be before <str:instance_id>/) ---
    path("<str:instance_id>/delete/",                               views.delete_instance,               name="delete"),
    path("<str:instance_id>/floating-ip/",                          views.assign_floating_ip,            name="assign_floating_ip"),
    path("<str:instance_id>/volume/create/",                        views.create_volume,                 name="create_volume"),
    path("<str:instance_id>/volume/<str:volume_id>/attach/",        views.attach_volume,                 name="attach_volume"),
    path("<str:instance_id>/volume/<str:volume_id>/detach/",        views.detach_volume,                 name="detach_volume"),
    path("<str:instance_id>/volume/<str:volume_id>/delete/",        views.delete_volume,                 name="delete_volume"),
    path("<str:instance_id>/security-groups/add/",                  views.add_instance_security_group,   name="sg_add"),
    path("<str:instance_id>/security-groups/remove/",               views.remove_instance_security_group, name="sg_remove"),

    # Instance detail — catch-all, must be last
    path("<str:instance_id>/", views.instance_detail, name="detail"),
]
