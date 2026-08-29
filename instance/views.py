import re
from django.shortcuts import render, redirect
from django.contrib import messages
from dashboard.views import get_openstack_connection


# ── friendly error helper (same approach as network app) ──

def _friendly(exc):
    raw = str(exc)
    if "409" in raw or "Conflict" in raw:
        if "attached" in raw.lower() or "in-use" in raw.lower():
            return "Volume is already attached to an instance."
        if "quota" in raw.lower():
            return "Volume quota exceeded. Delete unused volumes first."
        return "Conflict: the operation is not allowed in the current state."
    if "404" in raw or "not found" in raw.lower():
        return "Resource not found. It may have already been deleted."
    if "400" in raw or "Bad Request" in raw:
        return "Invalid request. Check all fields and try again."
    if "403" in raw or "Forbidden" in raw:
        return "Permission denied."
    # Strip HTTP URLs
    clean = re.sub(r'https?://\S+', '', raw).strip()
    for prefix in ("HttpException:", "ConflictException:", "NotFoundException:",
                   "BadRequestException:", "SDKException:", "Error:"):
        clean = clean.replace(prefix, "").strip()
    if len(clean) > 180:
        clean = clean[:180] + "…"
    return clean or "An unexpected error occurred. Please try again."


# =========================================================
# CREATE INSTANCE
# =========================================================

def create_instance(request):
    conn = get_openstack_connection()

    try:
        images  = list(conn.compute.images())
        flavors = list(conn.compute.flavors())
        networks = [
            {"id": n.id, "name": n.name or ""}
            for n in conn.network.networks()
            if not getattr(n, "is_router_external", False)
        ]
    except Exception as exc:
        messages.error(request, _friendly(exc))
        images = []
        flavors = []
        networks = []

    if request.method == "POST":
        name       = request.POST.get("name", "").strip()
        image_id   = request.POST.get("image", "").strip()
        flavor_id  = request.POST.get("flavor", "").strip()
        network_id = request.POST.get("network", "").strip()

        try:
            server = conn.compute.create_server(
                name=name,
                image_id=image_id,
                flavor_id=flavor_id,
                networks=[{"uuid": network_id}],
            )
            conn.compute.wait_for_server(server)
            messages.success(request, f"Instance '{name}' created successfully.")
            return redirect("instances")

        except Exception as exc:
            return render(request, "instance/create.html", {
                "images":   images,
                "flavors":  flavors,
                "networks": networks,
                "error":    _friendly(exc),
            })

    return render(request, "instance/create.html", {
        "images":   images,
        "flavors":  flavors,
        "networks": networks,
    })


# =========================================================
# DELETE INSTANCE
# =========================================================

def delete_instance(request, instance_id):
    conn = get_openstack_connection()

    # Try to get the instance name for display
    instance_name = instance_id
    try:
        server = conn.compute.get_server(instance_id)
        if server:
            instance_name = server.name
    except Exception:
        pass

    if request.method == "POST":
        try:
            conn.compute.delete_server(instance_id, ignore_missing=False)
            messages.success(request, f"Instance '{instance_name}' deleted.")
            return redirect("instances")
        except Exception as exc:
            return render(request, "instance/delete.html", {
                "instance_id":   instance_id,
                "instance_name": instance_name,
                "error":         _friendly(exc),
            })

    return render(request, "instance/delete.html", {
        "instance_id":   instance_id,
        "instance_name": instance_name,
    })


# =========================================================
# INSTANCE DETAIL
# =========================================================

def instance_detail(request, instance_id):
    conn = get_openstack_connection()

    try:
        instance = conn.compute.get_server(instance_id)

        if not instance:
            return render(request, "instance/detail.html",
                          {"error": "Instance not found."})

        # Network info
        network_info = []
        if instance.addresses:
            for network_name, addresses in instance.addresses.items():
                for address in addresses:
                    ip_type = address.get("OS-EXT-IPS:type")
                    network_info.append({
                        "network_name": network_name,
                        "ip_address":   address.get("addr"),
                        "ip_version":   address.get("version"),
                        "ip_type":      ip_type,
                        "is_floating":  ip_type == "floating",
                        "is_fixed":     ip_type == "fixed",
                    })

        # All volumes — mark which are attached to this instance
        volumes = []
        for volume in conn.block_storage.volumes():
            attached_to_instance = any(
                a.get("server_id") == instance_id
                for a in (volume.attachments or [])
            )
            volumes.append({
                "id":                   volume.id,
                "name":                 volume.name or "",
                "size":                 volume.size,
                "status":               volume.status,
                "attachments":          volume.attachments or [],
                "attached_to_instance": attached_to_instance,
            })

        return render(request, "instance/detail.html", {
            "instance":    instance,
            "network_info": network_info,
            "volumes":     volumes,
        })

    except Exception as exc:
        return render(request, "instance/detail.html",
                      {"error": _friendly(exc)})


# =========================================================
# ASSIGN FLOATING IP
# =========================================================

def assign_floating_ip(request, instance_id):
    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("instance:detail", instance_id=instance_id)

    conn = get_openstack_connection()

    try:
        external_network = next(
            conn.network.networks(is_router_external=True), None
        )
        if not external_network:
            messages.error(request,
                "No external network available. "
                "Configure an external Neutron network first.")
            return redirect("instance:detail", instance_id=instance_id)

        ports = list(conn.network.ports(device_id=instance_id))
        if not ports:
            messages.error(request,
                "This instance has no network port. "
                "Make sure it is connected to a network.")
            return redirect("instance:detail", instance_id=instance_id)

        # Check for existing floating IP on any port
        for port in ports:
            existing = list(conn.network.ips(port_id=port.id))
            if existing:
                messages.warning(request,
                    f"Instance already has floating IP: "
                    f"{existing[0].floating_ip_address}")
                return redirect("instance:detail", instance_id=instance_id)

        floating_ip = conn.network.create_ip(
            floating_network_id=external_network.id
        )
        conn.network.update_ip(floating_ip.id, port_id=ports[0].id)

        messages.success(request,
            f"Floating IP {floating_ip.floating_ip_address} assigned.")

    except Exception as exc:
        messages.error(request, f"Could not assign floating IP: {_friendly(exc)}")

    return redirect("instance:detail", instance_id=instance_id)


# =========================================================
# CREATE VOLUME  (create + optionally auto-attach)
# =========================================================

def create_volume(request, instance_id):
    conn = get_openstack_connection()

    # Load available volume types for the dropdown
    try:
        volume_types = [
            {"id": vt.id, "name": vt.name}
            for vt in conn.block_storage.types()
        ]
    except Exception:
        volume_types = []

    if request.method == "POST":
        volume_name  = request.POST.get("name", "").strip()
        size         = request.POST.get("size", "").strip()
        volume_type  = request.POST.get("volume_type", "").strip()
        auto_attach  = request.POST.get("auto_attach") == "on"

        if not size or not size.isdigit() or int(size) < 1:
            messages.error(request, "Please enter a valid size (minimum 1 GB).")
            return render(request, "instance/create_volume.html", {
                "instance_id":  instance_id,
                "volume_types": volume_types,
                "form_data":    request.POST,
            })

        try:
            kwargs = {"name": volume_name or "volume", "size": int(size)}
            if volume_type:
                kwargs["volume_type"] = volume_type

            volume = conn.block_storage.create_volume(**kwargs)

            if auto_attach:
                # Poll until volume is available (max 60 s)
                import time
                for _ in range(12):
                    vol = conn.block_storage.get_volume(volume.id)
                    if vol and vol.status == "available":
                        break
                    time.sleep(5)

                try:
                    server = conn.compute.get_server(instance_id)
                    conn.compute.create_volume_attachment(
                        server, volume_id=volume.id
                    )
                    messages.success(request,
                        f"Volume '{volume_name or 'volume'}' created and attached.")
                except Exception as exc:
                    messages.warning(request,
                        f"Volume created but could not attach: {_friendly(exc)}")
            else:
                messages.success(request,
                    f"Volume '{volume_name or 'volume'}' created. "
                    "You can attach it from the storage table.")

            return redirect("instance:detail", instance_id=instance_id)

        except Exception as exc:
            messages.error(request, f"Could not create volume: {_friendly(exc)}")
            return render(request, "instance/create_volume.html", {
                "instance_id":  instance_id,
                "volume_types": volume_types,
                "form_data":    request.POST,
            })

    return render(request, "instance/create_volume.html", {
        "instance_id":  instance_id,
        "volume_types": volume_types,
        "form_data":    {},
    })


# =========================================================
# ATTACH VOLUME
# =========================================================

def attach_volume(request, instance_id, volume_id):
    if request.method != "POST":
        return redirect("instance:detail", instance_id=instance_id)

    conn = get_openstack_connection()
    try:
        server = conn.compute.get_server(instance_id)
        volume = conn.block_storage.get_volume(volume_id)

        if not server:
            messages.error(request, "Instance not found.")
            return redirect("instance:detail", instance_id=instance_id)
        if not volume:
            messages.error(request, "Volume not found.")
            return redirect("instance:detail", instance_id=instance_id)
        if volume.status != "available":
            messages.error(request,
                f"Volume cannot be attached — current status is '{volume.status}'. "
                "Only 'available' volumes can be attached.")
            return redirect("instance:detail", instance_id=instance_id)

        conn.compute.create_volume_attachment(server, volume_id=volume.id)
        messages.success(request,
            f"Volume '{volume.name or volume.id}' attached successfully.")

    except Exception as exc:
        messages.error(request, f"Could not attach volume: {_friendly(exc)}")

    return redirect("instance:detail", instance_id=instance_id)


# =========================================================
# DETACH VOLUME
# =========================================================

def detach_volume(request, instance_id, volume_id):
    if request.method != "POST":
        return redirect("instance:detail", instance_id=instance_id)

    conn = get_openstack_connection()
    try:
        server = conn.compute.get_server(instance_id)
        volume = conn.block_storage.get_volume(volume_id)

        if not server:
            messages.error(request, "Instance not found.")
            return redirect("instance:detail", instance_id=instance_id)
        if not volume:
            messages.error(request, "Volume not found.")
            return redirect("instance:detail", instance_id=instance_id)

        # Find the attachment ID for this server + volume pair
        attachment_id = None
        for att in (volume.attachments or []):
            if att.get("server_id") == instance_id:
                attachment_id = att.get("attachment_id") or att.get("id")
                break

        if attachment_id:
            conn.compute.delete_volume_attachment(server, attachment_id)
        else:
            # Fallback: use volume_id directly
            conn.compute.delete_volume_attachment(server, volume_id)

        messages.success(request,
            f"Volume '{volume.name or volume.id}' detached successfully.")

    except Exception as exc:
        messages.error(request, f"Could not detach volume: {_friendly(exc)}")

    return redirect("instance:detail", instance_id=instance_id)


# =========================================================
# DELETE VOLUME
# =========================================================

def delete_volume(request, instance_id, volume_id):
    if request.method != "POST":
        return redirect("instance:detail", instance_id=instance_id)

    conn = get_openstack_connection()
    try:
        volume = conn.block_storage.get_volume(volume_id)

        if not volume:
            messages.error(request, "Volume not found.")
            return redirect("instance:detail", instance_id=instance_id)

        if volume.status == "in-use":
            messages.error(request,
                "Cannot delete an attached volume. Detach it first.")
            return redirect("instance:detail", instance_id=instance_id)

        vname = volume.name or volume_id
        conn.block_storage.delete_volume(volume_id, ignore_missing=False)
        messages.success(request, f"Volume '{vname}' deleted successfully.")

    except Exception as exc:
        messages.error(request, f"Could not delete volume: {_friendly(exc)}")

    return redirect("instance:detail", instance_id=instance_id)
