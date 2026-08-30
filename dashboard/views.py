import json
import time
import openstack

from django.contrib import messages
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, redirect


# -------------------------------------------------------
# OpenStack connection
# We connect using the "devstack-admin" profile defined
# in clouds.yaml. Every view calls this to get a connection.
# -------------------------------------------------------

def get_openstack_connection():
    return openstack.connect(cloud="devstack-admin")


# -------------------------------------------------------
# HOME PAGE
# Fetches all main resources from OpenStack and passes
# counts and breakdowns to the dashboard template.
# -------------------------------------------------------

def home(request):
    conn = get_openstack_connection()

    # Fetch raw data from OpenStack
    servers      = list(conn.compute.servers())
    volumes      = list(conn.block_storage.volumes())
    floating_ips = list(conn.network.ips())

    # Networks — we convert SDK objects to plain dicts because
    # Django templates cannot access attributes that start with
    # an underscore, and some SDK objects use those internally.
    networks = []
    for n in conn.network.networks():
        networks.append({
            "id":                 n.id,
            "name":               n.name or "",
            "status":             n.status or "",
            "is_router_external": getattr(n, "is_router_external", False),
        })

    # Routers — same reason: convert to plain dicts
    routers = []
    for r in conn.network.routers():
        gateway = r.external_gateway_info or {}
        routers.append({
            "id":            r.id,
            "name":          r.name or "",
            "status":        r.status or "",
            "gw_network_id": gateway.get("network_id", ""),
            "gw_snat":       gateway.get("enable_snat", False),
        })

    # Count instances by status for the dashboard charts
    instance_active  = sum(1 for s in servers if s.status == "ACTIVE")
    instance_shutoff = sum(1 for s in servers if s.status == "SHUTOFF")
    instance_error   = sum(1 for s in servers if s.status == "ERROR")
    instance_other   = len(servers) - instance_active - instance_shutoff - instance_error

    # Build a simple list of the 8 most recent instances
    # with the key fields the template needs
    recent_instances = []
    for server in servers[:8]:
        # Collect all IP addresses for this server
        ip_addresses = []
        if server.addresses:
            for network_name, addr_list in server.addresses.items():
                for addr in addr_list:
                    if addr.get("addr"):
                        ip_addresses.append(addr["addr"])

        # Get a readable flavor name
        flavor_name = "Unknown"
        if server.flavor:
            flavor_name = server.flavor.get("original_name") or server.flavor.get("id", "Unknown")

        recent_instances.append({
            "id":        server.id,
            "name":      server.name,
            "status":    server.status,
            "flavor":    flavor_name,
            "addresses": ip_addresses,
        })

    # Volume breakdowns for the storage chart
    volume_available = sum(1 for v in volumes if v.status == "available")
    volume_inuse     = sum(1 for v in volumes if v.status == "in-use")
    volume_error     = sum(1 for v in volumes if v.status == "error")
    volume_total_gb  = sum(v.size or 0 for v in volumes)

    # Network breakdowns
    networks_external = sum(1 for n in networks if n["is_router_external"])
    networks_internal = len(networks) - networks_external

    # Floating IP breakdowns
    fip_assigned = sum(
        1 for f in floating_ips
        if getattr(f, "port_id", None) or getattr(f, "fixed_ip_address", None)
    )
    fip_free = len(floating_ips) - fip_assigned

    return render(request, "dashboard/home.html", {
        "instances":         servers,
        "networks":          networks,
        "volumes":           volumes,
        "floating_ips":      floating_ips,
        "routers":           routers,
        "instance_active":   instance_active,
        "instance_shutoff":  instance_shutoff,
        "instance_error":    instance_error,
        "instance_other":    instance_other,
        "recent_instances":  recent_instances,
        "volume_available":  volume_available,
        "volume_inuse":      volume_inuse,
        "volume_error":      volume_error,
        "volume_total_gb":   volume_total_gb,
        "networks_external": networks_external,
        "networks_internal": networks_internal,
        "fip_assigned":      fip_assigned,
        "fip_free":          fip_free,
    })


# -------------------------------------------------------
# INSTANCES LIST PAGE
# Shows all servers in a table with status, IPs, and flavor.
# -------------------------------------------------------

def instances(request):
    conn = get_openstack_connection()

    instance_list = []
    for server in conn.compute.servers():

        # Collect all IP addresses across all networks
        ip_addresses = []
        if server.addresses:
            for network_name, addr_list in server.addresses.items():
                for addr in addr_list:
                    if addr.get("addr"):
                        ip_addresses.append(addr["addr"])

        # Prefer the human-readable flavor name over the ID
        flavor_name = "Unknown"
        if server.flavor:
            flavor_name = server.flavor.get("original_name") or server.flavor.get("id", "Unknown")

        instance_list.append({
            "id":        server.id,
            "name":      server.name,
            "status":    server.status,
            "addresses": ip_addresses,
            "flavor":    flavor_name,
        })

    return render(request, "dashboard/instances.html", {"instances": instance_list})


# -------------------------------------------------------
# IMAGES LIST PAGE
# Shows all Glance images with size, format, and visibility.
# -------------------------------------------------------

def images(request):
    conn = get_openstack_connection()

    image_list = []
    for img in conn.image.images():

        # Convert size from bytes to megabytes for display
        size_mb = None
        if getattr(img, "size", None):
            size_mb = round(img.size / (1024 * 1024), 1)

        image_list.append({
            "id":          img.id,
            "name":        img.name or "(unnamed)",
            "status":      img.status,
            "visibility":  getattr(img, "visibility", ""),
            "disk_format": getattr(img, "disk_format", ""),
            "min_disk":    getattr(img, "min_disk", 0),
            "min_ram":     getattr(img, "min_ram", 0),
            "size_mb":     size_mb,
            "created_at":  getattr(img, "created_at", None),
        })

    return render(request, "dashboard/images.html", {"images": image_list})


# -------------------------------------------------------
# CLEANUP — CONFIRMATION PAGE (GET)
# Before running cleanup, we show the user a summary of
# what will be deleted: the instance, its volumes, and
# any floating IPs assigned to it.
# -------------------------------------------------------

def cleanup_instance(request, instance_id):
    conn = get_openstack_connection()

    # GET: show the confirmation page
    if request.method == "GET":
        try:
            server = conn.compute.get_server(instance_id)
            if not server:
                messages.error(request, "Instance not found.")
                return redirect("instances")

            # Find volumes attached to this instance
            volumes_info = []
            for vol_ref in (getattr(server, "attached_volumes", None) or []):
                vol_id = vol_ref.get("id") or vol_ref.get("volume_id")
                if vol_id:
                    vol = conn.block_storage.get_volume(vol_id)
                    if vol:
                        volumes_info.append({
                            "id":   vol.id,
                            "name": vol.name or "Unnamed",
                            "size": vol.size,
                        })

            # Find floating IPs by matching fixed IPs to this server's addresses
            server_ips = set()
            for net_name, addr_list in (server.addresses or {}).items():
                for addr in addr_list:
                    if addr.get("addr"):
                        server_ips.add(addr["addr"])

            fips_info = []
            for fip in conn.network.ips():
                if getattr(fip, "fixed_ip_address", None) in server_ips:
                    fips_info.append({
                        "id":          fip.id,
                        "floating_ip": fip.floating_ip_address or "",
                        "fixed_ip":    fip.fixed_ip_address,
                    })

            # Collect IP addresses for the summary card
            display_ips = [
                addr["addr"]
                for net_name, addr_list in (server.addresses or {}).items()
                for addr in addr_list
                if addr.get("addr")
            ]

            flavor_name = ""
            if server.flavor:
                flavor_name = server.flavor.get("original_name") or server.flavor.get("id", "")

            return render(request, "dashboard/cleanup.html", {
                "instance_id":     instance_id,
                "instance_name":   server.name,
                "instance_status": server.status,
                "flavor_name":     flavor_name,
                "addresses":       display_ips,
                "volumes_info":    volumes_info,
                "fips_info":       fips_info,
            })

        except Exception as e:
            messages.error(request, f"Could not load instance: {e}")
            return redirect("instances")

    # POST: run the actual cleanup and stream progress back
    # using Server-Sent Events so the browser can show each
    # step as it completes in real time.
    return StreamingHttpResponse(
        _run_cleanup(instance_id),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _run_cleanup(instance_id):
    """
    Generator function that runs the cleanup step by step.
    Each step yields an SSE-formatted message so the browser
    can display progress as it happens.

    The cleanup order is important:
      1. Disassociate floating IPs  (must happen before instance deletion)
      2. Delete the instance        (must happen before volumes can be deleted)
      3. Delete attached volumes    (volumes can only be deleted when not in-use)
      4. Release floating IPs       (delete them from the pool)
    """

    # Helper that formats a message in SSE format
    def sse(event, data):
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    conn = get_openstack_connection()

    try:
        server = conn.compute.get_server(instance_id)
        if not server:
            yield sse("step", {"label": "Find instance", "status": "error", "detail": "Instance not found"})
            yield sse("done", {"ok": False, "name": instance_id})
            return

        instance_name = server.name

        # --- Collect resources to delete ---

        # Volume IDs attached to this server
        volume_ids = []
        for vol_ref in (getattr(server, "attached_volumes", None) or []):
            vid = vol_ref.get("id") or vol_ref.get("volume_id")
            if vid:
                volume_ids.append(vid)

        # Floating IPs assigned to this server
        server_ips = set()
        for net_name, addr_list in (server.addresses or {}).items():
            for addr in addr_list:
                if addr.get("addr"):
                    server_ips.add(addr["addr"])

        floating_ips = []
        for fip in conn.network.ips():
            if getattr(fip, "fixed_ip_address", None) in server_ips:
                floating_ips.append(fip)

        # Tell the browser how many steps to expect
        total_steps = 1 + len(volume_ids) + len(floating_ips) * 2
        yield sse("plan", {
            "total":   total_steps,
            "name":    instance_name,
            "volumes": len(volume_ids),
            "fips":    len(floating_ips),
        })

        # --- Step 1: Disassociate floating IPs ---
        # We detach the floating IP from its port so the instance
        # can be deleted cleanly. We delete it from the pool later.
        for fip in floating_ips:
            fip_addr = fip.floating_ip_address or fip.id
            try:
                conn.network.update_ip(fip.id, port_id=None)
                yield sse("step", {"label": f"Disassociate {fip_addr}", "status": "ok", "detail": "Detached from port"})
            except Exception as e:
                yield sse("step", {"label": f"Disassociate {fip_addr}", "status": "warning", "detail": str(e)})

        # --- Step 2: Delete the instance ---
        try:
            conn.compute.delete_server(instance_id, ignore_missing=True)
            # Wait up to 3 minutes for the server to disappear
            for _ in range(36):
                try:
                    still_exists = conn.compute.get_server(instance_id)
                    if still_exists is None:
                        break
                except Exception:
                    break
                time.sleep(5)
            yield sse("step", {"label": f"Delete instance '{instance_name}'", "status": "ok", "detail": "Instance removed"})
        except Exception as e:
            yield sse("step", {"label": f"Delete instance '{instance_name}'", "status": "error", "detail": str(e)})

        # --- Step 3: Delete attached volumes ---
        # Now that the instance is gone, the volumes are released
        # and can be deleted.
        for vid in volume_ids:
            vol = conn.block_storage.get_volume(vid)
            if not vol:
                yield sse("step", {"label": f"Delete volume {vid[:8]}…", "status": "warning", "detail": "Already gone"})
                continue
            vol_name = vol.name or vid
            try:
                conn.block_storage.delete_volume(vid, ignore_missing=True)
                # Wait for the volume to be deleted (up to 2 minutes)
                for _ in range(24):
                    try:
                        still_exists = conn.block_storage.get_volume(vid)
                        if still_exists is None:
                            break
                    except Exception:
                        break
                    time.sleep(5)
                yield sse("step", {"label": f"Delete volume '{vol_name}'", "status": "ok", "detail": f"{vol.size} GB freed"})
            except Exception as e:
                yield sse("step", {"label": f"Delete volume '{vol_name}'", "status": "error", "detail": str(e)})

        # --- Step 4: Release floating IPs ---
        # Remove the IPs from the project so they go back to the pool.
        for fip in floating_ips:
            fip_addr = fip.floating_ip_address or fip.id
            try:
                conn.network.delete_ip(fip.id, ignore_missing=True)
                yield sse("step", {"label": f"Release {fip_addr}", "status": "ok", "detail": "Returned to pool"})
            except Exception as e:
                yield sse("step", {"label": f"Release {fip_addr}", "status": "warning", "detail": str(e)})

        yield sse("done", {"ok": True, "name": instance_name})

    except Exception as e:
        yield sse("step", {"label": "Cleanup", "status": "error", "detail": str(e)})
        yield sse("done", {"ok": False, "name": instance_id})


# -------------------------------------------------------
# CLEANUP STATUS CHECK
# Called by the cleanup page to check if the instance
# is still there. Returns JSON.
# -------------------------------------------------------

def cleanup_status(request, instance_id):
    conn = get_openstack_connection()
    try:
        server = conn.compute.get_server(instance_id)
        if server:
            return JsonResponse({"exists": True, "status": server.status})
        return JsonResponse({"exists": False, "status": "DELETED"})
    except Exception:
        return JsonResponse({"exists": False, "status": "DELETED"})
