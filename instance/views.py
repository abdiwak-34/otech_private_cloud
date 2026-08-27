from django.shortcuts import render, redirect
from dashboard.views import get_openstack_connection


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