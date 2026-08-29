import openstack

from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.shortcuts import redirect


def get_openstack_connection():
    return openstack.connect(cloud="devstack-admin")


# =========================================================
# HOME / DASHBOARD
# =========================================================

def home(request):
    conn = get_openstack_connection()

    # ── Raw data ──────────────────────────────────────────
    instances    = list(conn.compute.servers())
    networks     = list(conn.network.networks())
    volumes      = list(conn.block_storage.volumes())
    floating_ips = list(conn.network.ips())
    routers      = list(conn.network.routers())

    # ── Instance stats ────────────────────────────────────
    instance_active  = sum(1 for s in instances if s.status == "ACTIVE")
    instance_shutoff = sum(1 for s in instances if s.status == "SHUTOFF")
    instance_error   = sum(1 for s in instances if s.status == "ERROR")
    instance_other   = len(instances) - instance_active - instance_shutoff - instance_error

    # Build a richer list for the recent-instances table (up to 8)
    recent_instances = []
    for server in instances[:8]:
        addrs = []
        if server.addresses:
            for net_name, net_addrs in server.addresses.items():
                for addr in net_addrs:
                    if addr.get("addr"):
                        addrs.append(addr["addr"])

        flavor_name = "—"
        if server.flavor:
            flavor_name = (
                server.flavor.get("original_name")
                or server.flavor.get("id", "—")
            )

        recent_instances.append({
            "id":        server.id,
            "name":      server.name,
            "status":    server.status,
            "flavor":    flavor_name,
            "addresses": addrs,
            "created":   getattr(server, "created", None),
        })

    # ── Volume stats ──────────────────────────────────────
    volume_available = sum(1 for v in volumes if v.status == "available")
    volume_inuse     = sum(1 for v in volumes if v.status == "in-use")
    volume_error     = sum(1 for v in volumes if v.status == "error")
    volume_total_gb  = sum(getattr(v, "size", 0) or 0 for v in volumes)

    # ── Network stats ─────────────────────────────────────
    networks_external = sum(1 for n in networks if getattr(n, "is_router_external", False))
    networks_internal = len(networks) - networks_external

    # ── Floating IP stats ─────────────────────────────────
    fip_assigned = sum(
        1 for f in floating_ips
        if getattr(f, "port_id", None) or getattr(f, "fixed_ip_address", None)
    )
    fip_free = len(floating_ips) - fip_assigned

    return render(
        request,
        "dashboard/home.html",
        {
            # raw lists (for length filters in template)
            "instances":    instances,
            "networks":     networks,
            "volumes":      volumes,
            "floating_ips": floating_ips,
            "routers":      routers,
            # instance breakdown
            "instance_active":  instance_active,
            "instance_shutoff": instance_shutoff,
            "instance_error":   instance_error,
            "instance_other":   instance_other,
            "recent_instances": recent_instances,
            # volume breakdown
            "volume_available": volume_available,
            "volume_inuse":     volume_inuse,
            "volume_error":     volume_error,
            "volume_total_gb":  volume_total_gb,
            # network breakdown
            "networks_external": networks_external,
            "networks_internal": networks_internal,
            # floating IP breakdown
            "fip_assigned": fip_assigned,
            "fip_free":     fip_free,
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

@require_POST
def cleanup_instance(request, instance_id):

    conn = get_openstack_connection()

    try:

        # =====================================================
        # 1. GET INSTANCE
        # =====================================================

        server = conn.compute.get_server(instance_id)

        if not server:
            messages.error(
                request,
                "Instance was not found."
            )

            return redirect("instances")


        instance_name = server.name


        # =====================================================
        # 2. FIND ATTACHED VOLUMES
        # =====================================================

        volume_ids = []

        attached_volumes = getattr(
            server,
            "attached_volumes",
            None
        )

        if attached_volumes:

            for volume in attached_volumes:

                volume_id = volume.get("id")

                if not volume_id:
                    volume_id = volume.get("volume_id")

                if volume_id:
                    volume_ids.append(volume_id)


        # =====================================================
        # 3. FIND FLOATING IPs
        # =====================================================

        floating_ips_to_release = []

        try:

            floating_ips = list(
                conn.network.ips()
            )

            server_addresses = (
                server.addresses or {}
            )

            server_ip_addresses = set()

            for network_name, addresses in server_addresses.items():

                for address in addresses:

                    ip_address = address.get(
                        "addr"
                    )

                    if ip_address:
                        server_ip_addresses.add(
                            ip_address
                        )


            for floating_ip in floating_ips:

                fixed_ip = getattr(
                    floating_ip,
                    "fixed_ip",
                    None
                )

                if fixed_ip in server_ip_addresses:

                    floating_ips_to_release.append(
                        floating_ip
                    )

        except Exception as exc:

            print(
                f"Could not find floating IPs: {exc}"
            )


        # =====================================================
        # 4. DISASSOCIATE FLOATING IPs
        # =====================================================

        for floating_ip in floating_ips_to_release:

            try:

                conn.network.update_ip(
                    floating_ip,
                    port_id=None,
                    fixed_ip=None
                )

            except Exception as exc:

                print(
                    f"Could not release floating IP "
                    f"{floating_ip.id}: {exc}"
                )


        # =====================================================
        # 5. DELETE INSTANCE
        # =====================================================

        conn.compute.delete_server(
            server,
            ignore_missing=True,
            wait=True
        )


        # =====================================================
        # 6. DELETE ATTACHED VOLUMES
        # =====================================================

        deleted_volumes = 0

        for volume_id in volume_ids:

            try:

                volume = conn.block_storage.get_volume(
                    volume_id
                )

                if volume:

                    conn.block_storage.delete_volume(
                        volume,
                        ignore_missing=True,
                        wait=True
                    )

                    deleted_volumes += 1

            except Exception as exc:

                print(
                    f"Could not delete volume "
                    f"{volume_id}: {exc}"
                )


        # =====================================================
        # 7. DELETE FLOATING IPs
        # =====================================================

        deleted_floating_ips = 0

        for floating_ip in floating_ips_to_release:

            try:

                conn.network.delete_ip(
                    floating_ip,
                    ignore_missing=True
                )

                deleted_floating_ips += 1

            except Exception as exc:

                print(
                    f"Could not delete floating IP "
                    f"{floating_ip.id}: {exc}"
                )


        # =====================================================
        # 8. SUCCESS MESSAGE
        # =====================================================

        messages.success(
            request,
            f"Cleanup completed for '{instance_name}'. "
            f"Instance deleted, "
            f"{deleted_volumes} volume(s) deleted, "
            f"and {deleted_floating_ips} floating IP(s) released."
        )

    except Exception as exc:

        messages.error(
            request,
            f"Cleanup failed for instance: {exc}"
        )

    return redirect("instances")

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
# NETWORKS — redirect to the full network app
# =========================================================

def networks(request):
    return redirect("network:list")