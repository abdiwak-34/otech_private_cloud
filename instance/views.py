from django.shortcuts import render, redirect
from dashboard.views import get_openstack_connection
from django.contrib import messages


def create_instance(request):

    conn = get_openstack_connection()

    images = list(conn.compute.images())
    flavors = list(conn.compute.flavors())
    networks = list(conn.network.networks())

    if request.method == "POST":

        name = request.POST.get("name")
        image_id = request.POST.get("image")
        flavor_id = request.POST.get("flavor")
        network_id = request.POST.get("network")

        try:

            server = conn.compute.create_server(
                name=name,
                image_id=image_id,
                flavor_id=flavor_id,
                networks=[
                    {
                        "uuid": network_id
                    }
                ]
            )

            # Wait until the VM is actually created
            server = conn.compute.wait_for_server(
                server
            )

            return redirect("instances")

        except Exception as e:

            error = str(e)

            return render(
                request,
                "instance/create.html",
                {
                    "images": images,
                    "flavors": flavors,
                    "networks": networks,
                    "error": error,
                }
            )

    return render(
        request,
        "instance/create.html",
        {
            "images": images,
            "flavors": flavors,
            "networks": networks,
        }
    )

def delete_instance(request, instance_id):

    conn = get_openstack_connection()

    if request.method == "POST":

        try:

            conn.compute.delete_server(
                instance_id,
                ignore_missing=False
            )

            return redirect("instances")

        except Exception as e:

            return render(
                request,
                "instance/delete.html",
                {
                    "instance_id": instance_id,
                    "error": str(e),
                }
            )

    return render(
        request,
        "instance/delete.html",
        {
            "instance_id": instance_id,
        }
    )

def instance_detail(request, instance_id):

    conn = get_openstack_connection()

    try:

        # =====================================================
        # GET INSTANCE
        # =====================================================

        instance = conn.compute.get_server(instance_id)

        if not instance:
            return render(
                request,
                "instance/detail.html",
                {
                    "error": "Instance not found."
                }
            )


        # =====================================================
        # NETWORK INFORMATION
        # =====================================================

        network_info = []

        if instance.addresses:

            for network_name, addresses in instance.addresses.items():

                for address in addresses:

                    ip_type = address.get("OS-EXT-IPS:type")

                    network_info.append({
                        "network_name": network_name,
                        "ip_address": address.get("addr"),
                        "ip_version": address.get("version"),
                        "ip_type": ip_type,
                        "is_floating": ip_type == "floating",
                        "is_fixed": ip_type == "fixed",
                    })


        # =====================================================
        # GET ALL VOLUMES
        # =====================================================

        volumes = []

        for volume in conn.block_storage.volumes():

            attached_to_instance = False

            if volume.attachments:

                for attachment in volume.attachments:

                    if attachment.get("server_id") == instance_id:

                        attached_to_instance = True

                        break


            # Convert OpenStack object into simple dictionary
            volumes.append({
                "id": volume.id,
                "name": volume.name,
                "size": volume.size,
                "status": volume.status,
                "attachments": volume.attachments,
                "attached_to_instance": attached_to_instance,
            })


        # =====================================================
        # RENDER
        # =====================================================

        return render(
            request,
            "instance/detail.html",
            {
                "instance": instance,
                "network_info": network_info,
                "volumes": volumes,
            }
        )


    except Exception as e:

        return render(
            request,
            "instance/detail.html",
            {
                "error": str(e)
            }
        )


def assign_floating_ip(request, instance_id):

    if request.method != "POST":
        messages.error(
            request,
            "Invalid request. Please use the Assign Floating IP button."
        )

        return redirect(
            "instance:detail",
            instance_id=instance_id
        )

    conn = get_openstack_connection()

    try:

        # =====================================================
        # GET EXTERNAL NETWORK
        # =====================================================

        external_network = next(
            conn.network.networks(
                is_router_external=True
            ),
            None
        )

        if not external_network:

            messages.error(
                request,
                "No external network is available. "
                "Create/configure an external Neutron network first."
            )

            return redirect(
                "instance:detail",
                instance_id=instance_id
            )


        # =====================================================
        # FIND INSTANCE PORT
        # =====================================================

        ports = list(
            conn.network.ports(
                device_id=instance_id
            )
        )

        if not ports:

            messages.error(
                request,
                "This instance does not have a network port."
            )

            return redirect(
                "instance:detail",
                instance_id=instance_id
            )


        # =====================================================
        # CHECK EXISTING FLOATING IP
        # =====================================================

        for port in ports:

            floating_ips = list(
                conn.network.ips(
                    port_id=port.id
                )
            )

            if floating_ips:

                messages.warning(
                    request,
                    f"This instance already has a floating IP: "
                    f"{floating_ips[0].floating_ip_address}"
                )

                return redirect(
                    "instance:detail",
                    instance_id=instance_id
                )


        # =====================================================
        # CREATE FLOATING IP
        # =====================================================

        floating_ip = conn.network.create_ip(
            floating_network_id=external_network.id
        )


        # =====================================================
        # ASSOCIATE FLOATING IP WITH PORT
        # =====================================================

        conn.network.update_ip(
            floating_ip.id,
            port_id=ports[0].id
        )


        # =====================================================
        # SUCCESS
        # =====================================================

        messages.success(
            request,
            f"Floating IP {floating_ip.floating_ip_address} "
            f"assigned successfully."
        )


    except Exception as e:

        messages.error(
            request,
            f"Failed to assign floating IP: {str(e)}"
        )


    return redirect(
        "instance:detail",
        instance_id=instance_id
    )

def create_volume(request, instance_id):

    if request.method == "POST":

        try:

            conn = get_openstack_connection()

            volume_name = request.POST.get("name")
            size = request.POST.get("size")

            volume = conn.block_storage.create_volume(
                name=volume_name,
                size=int(size)
            )

            return redirect(
                "instance:detail",
                instance_id=instance_id
            )

        except Exception as e:

            return render(
                request,
                "instance/create_volume.html",
                {
                    "instance_id": instance_id,
                    "error": str(e),
                }
            )

    return render(
        request,
        "instance/create_volume.html",
        {
            "instance_id": instance_id,
        }
    )

def attach_volume(request, instance_id, volume_id):

    conn = get_openstack_connection()

    try:

        server = conn.compute.get_server(instance_id)

        volume = conn.block_storage.get_volume(volume_id)

        if not server:
            messages.error(request, "Instance not found.")
            return redirect(
                "instance:detail",
                instance_id=instance_id
            )

        if not volume:
            messages.error(request, "Volume not found.")
            return redirect(
                "instance:detail",
                instance_id=instance_id
            )

        conn.compute.create_volume_attachment(
            server,
            volume_id=volume.id
        )

        messages.success(
            request,
            f"Volume '{volume.name}' attached successfully."
        )

    except Exception as e:

        messages.error(
            request,
            f"Failed to attach volume: {str(e)}"
        )

    return redirect(
        "instance:detail",
        instance_id=instance_id
    )

def detach_volume(request, instance_id, volume_id):

    conn = get_openstack_connection()

    try:

        server = conn.compute.get_server(instance_id)

        volume = conn.block_storage.get_volume(volume_id)

        if not server:
            messages.error(request, "Instance not found.")
            return redirect(
                "instance:detail",
                instance_id=instance_id
            )

        if not volume:
            messages.error(request, "Volume not found.")
            return redirect(
                "instance:detail",
                instance_id=instance_id
            )

        conn.compute.delete_volume_attachment(
            server,
            volume_id
        )

        messages.success(
            request,
            f"Volume '{volume.name}' detached successfully."
        )

    except Exception as e:

        messages.error(
            request,
            f"Failed to detach volume: {str(e)}"
        )

    return redirect(
        "instance:detail",
        instance_id=instance_id
    )

def delete_volume(request, instance_id, volume_id):

    conn = get_openstack_connection()

    try:

        volume = conn.block_storage.get_volume(volume_id)

        if not volume:

            messages.error(
                request,
                "Volume not found."
            )

            return redirect(
                "instance:detail",
                instance_id=instance_id
            )


        if volume.status == "in-use":

            messages.error(
                request,
                "Cannot delete an attached volume. Detach it first."
            )

            return redirect(
                "instance:detail",
                instance_id=instance_id
            )


        conn.block_storage.delete_volume(
            volume_id,
            ignore_missing=False
        )


        messages.success(
            request,
            f"Volume '{volume.name}' deleted successfully."
        )


    except Exception as e:

        messages.error(
            request,
            f"Failed to delete volume: {str(e)}"
        )


    return redirect(
        "instance:detail",
        instance_id=instance_id
    )