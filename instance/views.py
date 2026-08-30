import time
from django.contrib import messages
from django.shortcuts import render, redirect
from dashboard.views import get_openstack_connection


# -------------------------------------------------------
# Error message helper
# OpenStack SDK errors contain raw HTTP URLs and stack
# traces that confuse users. This function turns them into
# short, plain English messages instead.
# -------------------------------------------------------

def friendly_error(exc):
    text = str(exc)
    if "409" in text or "Conflict" in text:
        if "in-use" in text.lower() or "attached" in text.lower():
            return "This volume is already attached to an instance."
        if "quota" in text.lower():
            return "You have reached the volume quota. Delete unused volumes first."
        return "This operation is not allowed in the current state."
    if "404" in text or "not found" in text.lower():
        return "Resource not found. It may have already been deleted."
    if "400" in text or "Bad Request" in text:
        return "Invalid input. Please check all fields and try again."
    if "403" in text or "Forbidden" in text:
        return "You do not have permission to do that."
    # If none of the above matched, return the raw message
    # but cut it short so it doesn't fill the screen.
    return text[:200] if len(text) > 200 else text


# -------------------------------------------------------
# CREATE INSTANCE
# Shows a form with dropdowns for image, flavor, and network.
# On submit, creates the server and waits for it to be ready.
# -------------------------------------------------------

def create_instance(request):
    conn = get_openstack_connection()

    # Load the dropdown options from OpenStack
    images   = list(conn.compute.images())
    flavors  = list(conn.compute.flavors())

    # Only show internal (non-external) networks in the dropdown
    networks = [
        {"id": n.id, "name": n.name or ""}
        for n in conn.network.networks()
        if not getattr(n, "is_router_external", False)
    ]

    if request.method == "POST":
        name       = request.POST.get("name", "").strip()
        image_id   = request.POST.get("image", "").strip()
        flavor_id  = request.POST.get("flavor", "").strip()
        network_id = request.POST.get("network", "").strip()

        try:
            # Create the server via Nova
            server = conn.compute.create_server(
                name=name,
                image_id=image_id,
                flavor_id=flavor_id,
                networks=[{"uuid": network_id}],
            )
            # Block until the server reaches ACTIVE status
            conn.compute.wait_for_server(server)
            messages.success(request, f"Instance '{name}' created successfully.")
            return redirect("instances")

        except Exception as e:
            return render(request, "instance/create.html", {
                "images":   images,
                "flavors":  flavors,
                "networks": networks,
                "error":    friendly_error(e),
            })

    # GET: show the empty form
    return render(request, "instance/create.html", {
        "images":   images,
        "flavors":  flavors,
        "networks": networks,
    })


# -------------------------------------------------------
# DELETE INSTANCE
# Shows a confirmation page first. On confirm (POST),
# calls Nova to delete the server.
# -------------------------------------------------------

def delete_instance(request, instance_id):
    conn = get_openstack_connection()

    # Get the instance name so the confirmation page looks nice
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
        except Exception as e:
            return render(request, "instance/delete.html", {
                "instance_id":   instance_id,
                "instance_name": instance_name,
                "error":         friendly_error(e),
            })

    # GET: show the confirmation page
    return render(request, "instance/delete.html", {
        "instance_id":   instance_id,
        "instance_name": instance_name,
    })


# -------------------------------------------------------
# INSTANCE DETAIL
# Shows the instance's IP addresses and all Cinder volumes,
# marking which ones are attached to this specific instance.
# -------------------------------------------------------

def instance_detail(request, instance_id):
    conn = get_openstack_connection()

    try:
        instance = conn.compute.get_server(instance_id)
        if not instance:
            return render(request, "instance/detail.html", {"error": "Instance not found."})

        # Build the network / IP information for the networking section
        network_info = []
        if instance.addresses:
            for network_name, addr_list in instance.addresses.items():
                for addr in addr_list:
                    ip_type = addr.get("OS-EXT-IPS:type")
                    network_info.append({
                        "network_name": network_name,
                        "ip_address":   addr.get("addr"),
                        "ip_version":   addr.get("version"),
                        "is_floating":  ip_type == "floating",
                        "is_fixed":     ip_type == "fixed",
                    })

        # Get all volumes in the project and flag which ones
        # belong to this instance so the template can show
        # the right action buttons (attach / detach / delete).
        volumes = []
        for vol in conn.block_storage.volumes():
            is_attached_here = any(
                att.get("server_id") == instance_id
                for att in (vol.attachments or [])
            )
            volumes.append({
                "id":                   vol.id,
                "name":                 vol.name or "",
                "size":                 vol.size,
                "status":               vol.status,
                "attachments":          vol.attachments or [],
                "attached_to_instance": is_attached_here,
            })

        return render(request, "instance/detail.html", {
            "instance":    instance,
            "network_info": network_info,
            "volumes":     volumes,
        })

    except Exception as e:
        return render(request, "instance/detail.html", {"error": friendly_error(e)})


# -------------------------------------------------------
# ASSIGN FLOATING IP
# Allocates a new floating IP from the external network
# and associates it with the first port of this instance.
# -------------------------------------------------------

def assign_floating_ip(request, instance_id):
    if request.method != "POST":
        return redirect("instance:detail", instance_id=instance_id)

    conn = get_openstack_connection()

    try:
        # Find the external network (the one connected to the internet)
        external_network = next(conn.network.networks(is_router_external=True), None)
        if not external_network:
            messages.error(request, "No external network found. Ask your admin to configure one.")
            return redirect("instance:detail", instance_id=instance_id)

        # Find the network port(s) for this instance
        ports = list(conn.network.ports(device_id=instance_id))
        if not ports:
            messages.error(request, "This instance has no network port. Make sure it is connected to a network.")
            return redirect("instance:detail", instance_id=instance_id)

        # Check whether a floating IP is already assigned
        for port in ports:
            existing_fips = list(conn.network.ips(port_id=port.id))
            if existing_fips:
                messages.warning(request, f"This instance already has a floating IP: {existing_fips[0].floating_ip_address}")
                return redirect("instance:detail", instance_id=instance_id)

        # Allocate a new floating IP and link it to the first port
        new_fip = conn.network.create_ip(floating_network_id=external_network.id)
        conn.network.update_ip(new_fip.id, port_id=ports[0].id)

        messages.success(request, f"Floating IP {new_fip.floating_ip_address} assigned.")

    except Exception as e:
        messages.error(request, f"Could not assign floating IP: {friendly_error(e)}")

    return redirect("instance:detail", instance_id=instance_id)


# -------------------------------------------------------
# CREATE VOLUME
# Creates a new Cinder block storage volume. If the user
# checked "auto-attach", it also attaches it straight away.
# -------------------------------------------------------

def create_volume(request, instance_id):
    conn = get_openstack_connection()

    # Load volume types for the optional dropdown
    try:
        volume_types = [{"id": vt.id, "name": vt.name} for vt in conn.block_storage.types()]
    except Exception:
        volume_types = []

    if request.method == "POST":
        vol_name    = request.POST.get("name", "").strip()
        size        = request.POST.get("size", "").strip()
        vol_type    = request.POST.get("volume_type", "").strip()
        auto_attach = request.POST.get("auto_attach") == "on"

        # Validate the size before hitting the API
        if not size or not size.isdigit() or int(size) < 1:
            messages.error(request, "Please enter a valid size (1 GB or more).")
            return render(request, "instance/create_volume.html", {
                "instance_id":  instance_id,
                "volume_types": volume_types,
                "form_data":    request.POST,
            })

        try:
            # Create the volume via Cinder
            kwargs = {"name": vol_name or "volume", "size": int(size)}
            if vol_type:
                kwargs["volume_type"] = vol_type
            volume = conn.block_storage.create_volume(**kwargs)

            if auto_attach:
                # Wait up to 60 seconds for the volume to become available
                for _ in range(12):
                    vol = conn.block_storage.get_volume(volume.id)
                    if vol and vol.status == "available":
                        break
                    time.sleep(5)

                # Attach the volume to the instance
                server = conn.compute.get_server(instance_id)
                conn.compute.create_volume_attachment(server, volume_id=volume.id)
                messages.success(request, f"Volume '{vol_name or 'volume'}' created and attached.")
            else:
                messages.success(request, f"Volume '{vol_name or 'volume'}' created. Attach it from the storage table below.")

            return redirect("instance:detail", instance_id=instance_id)

        except Exception as e:
            messages.error(request, f"Could not create volume: {friendly_error(e)}")
            return render(request, "instance/create_volume.html", {
                "instance_id":  instance_id,
                "volume_types": volume_types,
                "form_data":    request.POST,
            })

    # GET: show the empty create form
    return render(request, "instance/create_volume.html", {
        "instance_id":  instance_id,
        "volume_types": volume_types,
        "form_data":    {},
    })


# -------------------------------------------------------
# ATTACH VOLUME
# Attaches an existing volume to this instance via Nova.
# The volume must be in "available" status to be attached.
# -------------------------------------------------------

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
            messages.error(request, f"Volume status is '{volume.status}'. Only 'available' volumes can be attached.")
            return redirect("instance:detail", instance_id=instance_id)

        conn.compute.create_volume_attachment(server, volume_id=volume.id)
        messages.success(request, f"Volume '{volume.name or volume.id}' attached.")

    except Exception as e:
        messages.error(request, f"Could not attach volume: {friendly_error(e)}")

    return redirect("instance:detail", instance_id=instance_id)


# -------------------------------------------------------
# DETACH VOLUME
# Removes the volume from this instance. The volume goes
# back to "available" status and can be reattached later.
# -------------------------------------------------------

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

        # Nova needs the attachment ID, not the volume ID.
        # We find it by looking through the volume's attachment records.
        attachment_id = None
        for att in (volume.attachments or []):
            if att.get("server_id") == instance_id:
                attachment_id = att.get("attachment_id") or att.get("id")
                break

        if attachment_id:
            conn.compute.delete_volume_attachment(server, attachment_id)
        else:
            # Fallback if we couldn't find the attachment ID
            conn.compute.delete_volume_attachment(server, volume_id)

        messages.success(request, f"Volume '{volume.name or volume.id}' detached.")

    except Exception as e:
        messages.error(request, f"Could not detach volume: {friendly_error(e)}")

    return redirect("instance:detail", instance_id=instance_id)


# -------------------------------------------------------
# DELETE VOLUME
# Permanently deletes a Cinder volume. The volume must be
# detached first — we refuse if it is still "in-use".
# -------------------------------------------------------

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
            messages.error(request, "Cannot delete a volume that is still attached. Detach it first.")
            return redirect("instance:detail", instance_id=instance_id)

        vol_name = volume.name or volume_id
        conn.block_storage.delete_volume(volume_id, ignore_missing=False)
        messages.success(request, f"Volume '{vol_name}' deleted.")

    except Exception as e:
        messages.error(request, f"Could not delete volume: {friendly_error(e)}")

    return redirect("instance:detail", instance_id=instance_id)
