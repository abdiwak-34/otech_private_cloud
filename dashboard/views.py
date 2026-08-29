import json
import openstack

from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, redirect
from django.contrib import messages


def get_openstack_connection():
    return openstack.connect(cloud="devstack-admin")


# =========================================================
# HOME / DASHBOARD
# =========================================================

def home(request):
    conn = get_openstack_connection()

    instances    = list(conn.compute.servers())
    raw_networks = list(conn.network.networks())
    volumes      = list(conn.block_storage.volumes())
    floating_ips = list(conn.network.ips())
    raw_routers  = list(conn.network.routers())

    # Convert to plain dicts so templates never hit SDK underscore attributes
    networks = [
        {
            "id":                 n.id,
            "name":               n.name or "",
            "status":             n.status or "",
            "is_router_external": getattr(n, "is_router_external", False),
        }
        for n in raw_networks
    ]

    routers = []
    for r in raw_routers:
        gw = r.external_gateway_info or {}
        routers.append({
            "id":            r.id,
            "name":          r.name or "",
            "status":        r.status or "",
            "gw_network_id": gw.get("network_id", ""),
            "gw_snat":       gw.get("enable_snat", False),
        })

    instance_active  = sum(1 for s in instances if s.status == "ACTIVE")
    instance_shutoff = sum(1 for s in instances if s.status == "SHUTOFF")
    instance_error   = sum(1 for s in instances if s.status == "ERROR")
    instance_other   = len(instances) - instance_active - instance_shutoff - instance_error

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

    volume_available = sum(1 for v in volumes if v.status == "available")
    volume_inuse     = sum(1 for v in volumes if v.status == "in-use")
    volume_error     = sum(1 for v in volumes if v.status == "error")
    volume_total_gb  = sum(getattr(v, "size", 0) or 0 for v in volumes)

    networks_external = sum(1 for n in networks if n["is_router_external"])
    networks_internal = len(networks) - networks_external

    fip_assigned = sum(
        1 for f in floating_ips
        if getattr(f, "port_id", None) or getattr(f, "fixed_ip_address", None)
    )
    fip_free = len(floating_ips) - fip_assigned

    return render(
        request,
        "dashboard/home.html",
        {
            "instances":         instances,
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
        }
    )


# =========================================================
# INSTANCES LIST
# =========================================================

def instances(request):
    conn = get_openstack_connection()

    instance_list = []

    for server in conn.compute.servers():
        addresses = []
        if server.addresses:
            for network_name, network_addresses in server.addresses.items():
                for address in network_addresses:
                    if address.get("addr"):
                        addresses.append(address.get("addr"))

        flavor_name = "Unknown"
        if server.flavor:
            flavor_name = server.flavor.get("original_name") or server.flavor.get("id", "Unknown")

        instance_list.append({
            "id":        server.id,
            "name":      server.name,
            "status":    server.status,
            "addresses": addresses,
            "flavor":    flavor_name,
        })

    return render(request, "dashboard/instances.html", {"instances": instance_list})


# =========================================================
# CLEANUP  — GET: confirmation/progress page
#            POST: execute cleanup, returns JSON
# =========================================================

def cleanup_instance(request, instance_id):
    """
    GET  → render the animated confirmation/progress page.
    POST → execute the full cleanup and return a JSON stream
           of step results so the page can animate each stage.
    """
    conn = get_openstack_connection()

    # ── GET: show the confirmation / progress page ─────────
    if request.method == "GET":
        try:
            server = conn.compute.get_server(instance_id)
            if not server:
                messages.error(request, "Instance not found.")
                return redirect("instances")

            # Collect what will be cleaned up so the page can
            # show the user exactly what is about to happen.
            volume_ids = []
            attached_volumes = getattr(server, "attached_volumes", None) or []
            for v in attached_volumes:
                vid = v.get("id") or v.get("volume_id")
                if vid:
                    volume_ids.append(vid)

            volumes_info = []
            for vid in volume_ids:
                try:
                    vol = conn.block_storage.get_volume(vid)
                    if vol:
                        volumes_info.append({"id": vol.id, "name": vol.name or "Unnamed", "size": vol.size})
                except Exception:
                    pass

            # Floating IPs attached to this instance
            fips_info = []
            try:
                server_ips = set()
                for net_name, addrs in (server.addresses or {}).items():
                    for a in addrs:
                        if a.get("addr"):
                            server_ips.add(a["addr"])

                for fip in conn.network.ips():
                    fip_fixed = getattr(fip, "fixed_ip_address", None)
                    fip_float = getattr(fip, "floating_ip_address", None)
                    if fip_fixed and fip_fixed in server_ips:
                        fips_info.append({
                            "id":         fip.id,
                            "floating_ip": fip_float or "unknown",
                            "fixed_ip":    fip_fixed,
                        })
            except Exception:
                pass

            # Addresses for display
            addrs_display = []
            if server.addresses:
                for net_name, addrs in server.addresses.items():
                    for a in addrs:
                        if a.get("addr"):
                            addrs_display.append(a["addr"])

            flavor_name = "—"
            if server.flavor:
                flavor_name = server.flavor.get("original_name") or server.flavor.get("id", "—")

            context = {
                "instance_id":   instance_id,
                "instance_name": server.name,
                "instance_status": server.status,
                "flavor_name":   flavor_name,
                "addresses":     addrs_display,
                "volumes_info":  volumes_info,
                "fips_info":     fips_info,
            }
            return render(request, "dashboard/cleanup.html", context)

        except Exception as exc:
            messages.error(request, f"Could not load instance: {exc}")
            return redirect("instances")

    # ── POST: stream cleanup steps via Server-Sent Events ──
    def event_stream():
        """Generator that yields SSE-formatted lines as each step completes."""

        def send(event_type, data):
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        conn2 = get_openstack_connection()

        try:
            server = conn2.compute.get_server(instance_id)
            if not server:
                yield send("step", {"label": "Find instance", "status": "error",
                                    "detail": "Instance not found"})
                yield send("done", {"ok": False, "name": instance_id})
                return

            instance_name = server.name

            # ── Collect volumes ──────────────────────────
            volume_ids = []
            for v in (getattr(server, "attached_volumes", None) or []):
                vid = v.get("id") or v.get("volume_id")
                if vid:
                    volume_ids.append(vid)

            # ── Collect floating IPs ─────────────────────
            server_ips = set()
            for net_name, addrs in (server.addresses or {}).items():
                for a in addrs:
                    if a.get("addr"):
                        server_ips.add(a["addr"])

            fips_to_delete = []
            try:
                for fip in conn2.network.ips():
                    if getattr(fip, "fixed_ip_address", None) in server_ips:
                        fips_to_delete.append(fip)
            except Exception as exc:
                yield send("step", {"label": "Scan floating IPs", "status": "warning",
                                    "detail": f"Could not scan: {exc}"})

            # ── Send a "plan" event so the JS knows total steps ──
            total = 1 + len(volume_ids) + len(fips_to_delete) * 2
            yield send("plan", {"total": total, "name": instance_name,
                                "volumes": len(volume_ids),
                                "fips": len(fips_to_delete)})

            # ── Step 1: Disassociate floating IPs ─────────
            for fip in fips_to_delete:
                fip_addr = getattr(fip, "floating_ip_address", fip.id)
                try:
                    conn2.network.update_ip(fip.id, port_id=None)
                    yield send("step", {
                        "label":  f"Disassociate {fip_addr}",
                        "status": "ok",
                        "detail": "Floating IP detached from port"
                    })
                except Exception as exc:
                    yield send("step", {
                        "label":  f"Disassociate {fip_addr}",
                        "status": "warning",
                        "detail": str(exc)
                    })

            # ── Step 2: Delete instance ───────────────────
            try:
                conn2.compute.delete_server(instance_id, ignore_missing=True)
                conn2.compute.wait_for_delete(server)
                yield send("step", {
                    "label":  f"Delete instance '{instance_name}'",
                    "status": "ok",
                    "detail": "Instance terminated and removed"
                })
            except Exception as exc:
                yield send("step", {
                    "label":  f"Delete instance '{instance_name}'",
                    "status": "error",
                    "detail": str(exc)
                })

            # ── Step 3: Delete volumes ────────────────────
            for vid in volume_ids:
                try:
                    vol = conn2.block_storage.get_volume(vid)
                    if vol:
                        vname = vol.name or vid
                        vsize = vol.size
                        conn2.block_storage.delete_volume(vid, ignore_missing=True)
                        conn2.block_storage.wait_for_delete(vol)
                        yield send("step", {
                            "label":  f"Delete volume '{vname}'",
                            "status": "ok",
                            "detail": f"{vsize} GB freed"
                        })
                    else:
                        yield send("step", {
                            "label":  f"Delete volume {vid[:8]}…",
                            "status": "warning",
                            "detail": "Volume not found (already deleted)"
                        })
                except Exception as exc:
                    yield send("step", {
                        "label":  f"Delete volume {vid[:8]}…",
                        "status": "error",
                        "detail": str(exc)
                    })

            # ── Step 4: Release floating IPs ─────────────
            for fip in fips_to_delete:
                fip_addr = getattr(fip, "floating_ip_address", fip.id)
                try:
                    conn2.network.delete_ip(fip.id, ignore_missing=True)
                    yield send("step", {
                        "label":  f"Release {fip_addr}",
                        "status": "ok",
                        "detail": "Returned to floating IP pool"
                    })
                except Exception as exc:
                    yield send("step", {
                        "label":  f"Release {fip_addr}",
                        "status": "warning",
                        "detail": str(exc)
                    })

            yield send("done", {"ok": True, "name": instance_name})

        except Exception as exc:
            yield send("step", {"label": "Cleanup", "status": "error", "detail": str(exc)})
            yield send("done", {"ok": False, "name": instance_id})

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"   # disable nginx buffering if proxied
    return response


# =========================================================
# CLEANUP STATUS — lightweight poll (kept for future use)
# =========================================================

def cleanup_status(request, instance_id):
    """Returns whether the instance still exists (used by progress page polling)."""
    conn = get_openstack_connection()
    try:
        server = conn.compute.get_server(instance_id)
        exists = server is not None
        status = server.status if server else "DELETED"
    except Exception:
        exists = False
        status = "DELETED"

    return JsonResponse({"exists": exists, "status": status})


# =========================================================
# IMAGES
# =========================================================

def images(request):
    conn = get_openstack_connection()

    raw = list(conn.image.images())

    image_list = []
    for img in raw:
        size_mb = None
        raw_size = getattr(img, "size", None)
        if raw_size:
            try:
                size_mb = round(int(raw_size) / (1024 * 1024), 1)
            except Exception:
                pass

        image_list.append({
            "id":         img.id,
            "name":       img.name or "(unnamed)",
            "status":     img.status,
            "visibility": getattr(img, "visibility", "—"),
            "disk_format": getattr(img, "disk_format", "—"),
            "min_disk":   getattr(img, "min_disk", 0),
            "min_ram":    getattr(img, "min_ram", 0),
            "size_mb":    size_mb,
            "created_at": getattr(img, "created_at", None),
            "owner":      getattr(img, "owner", "—"),
        })

    return render(request, "dashboard/images.html", {"images": image_list})
