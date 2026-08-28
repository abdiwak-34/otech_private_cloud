import openstack

from django.shortcuts import render


def get_openstack_connection():
    return openstack.connect(cloud="devstack-admin")


# =========================================================
# HOME / DASHBOARD
# =========================================================

def home(request):
    conn = get_openstack_connection()

    # Get all instances
    instances = list(conn.compute.servers())

    # Get all networks
    networks = list(conn.network.networks())

    # Get all volumes
    volumes = list(conn.block_storage.volumes())

    # Get all floating IPs
    floating_ips = list(conn.network.ips())

    return render(
        request,
        "dashboard/home.html",
        {
            "instances": instances,
            "networks": networks,
            "volumes": volumes,
            "floating_ips": floating_ips,
        }
    )


# =========================================================
# INSTANCES
# =========================================================

def instances(request):
    conn = get_openstack_connection()

    servers = conn.compute.servers()

    instance_list = []

    for server in servers:

        addresses = []

        if server.addresses:
            for network_name, network_addresses in server.addresses.items():

                for address in network_addresses:

                    if address.get("addr"):
                        addresses.append(address.get("addr"))

        # Get flavor name
        flavor_name = "Unknown"

        if server.flavor:

            flavor_name = server.flavor.get("original_name")

            if not flavor_name:
                flavor_name = server.flavor.get(
                    "id",
                    "Unknown"
                )

        instance_list.append(
            {
                "id": server.id,
                "name": server.name,
                "status": server.status,
                "addresses": addresses,
                "flavor": flavor_name,
            }
        )

    return render(
        request,
        "dashboard/instances.html",
        {
            "instances": instance_list
        }
    )


# =========================================================
# IMAGES
# =========================================================

def images(request):
    conn = get_openstack_connection()

    image_list = conn.image.images()

    return render(
        request,
        "dashboard/images.html",
        {
            "images": image_list
        }
    )


# =========================================================
# NETWORKS
# =========================================================

def networks(request):
    conn = get_openstack_connection()

    network_list = conn.network.networks()

    return render(
        request,
        "dashboard/networks.html",
        {
            "networks": network_list
        }
    )