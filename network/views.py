from django.shortcuts import render, redirect
from django.contrib import messages
from dashboard.views import get_openstack_connection


# =========================================================
# NETWORK LIST
# =========================================================

def network_list(request):
    conn = get_openstack_connection()

    try:
        networks = list(conn.network.networks())

        # Enrich each network with its subnets
        for network in networks:
            network._subnets = [
                conn.network.get_subnet(sid)
                for sid in (network.subnet_ids or [])
            ]

        routers = list(conn.network.routers())

    except Exception as exc:
        messages.error(request, f"Failed to load networks: {exc}")
        networks = []
        routers = []

    return render(
        request,
        "network/network_list.html",
        {
            "networks": networks,
            "routers": routers,
        },
    )


# =========================================================
# CREATE NETWORK
# =========================================================

def create_network(request):
    if request.method == "POST":
        name         = request.POST.get("name", "").strip()
        subnet_name  = request.POST.get("subnet_name", "").strip()
        cidr         = request.POST.get("cidr", "").strip()
        dns_servers  = request.POST.get("dns_servers", "").strip()
        enable_dhcp  = request.POST.get("enable_dhcp") == "on"

        conn = get_openstack_connection()

        try:
            # Create the network
            network = conn.network.create_network(name=name)

            # Build subnet kwargs
            subnet_kwargs = {
                "name":       subnet_name or f"{name}-subnet",
                "network_id": network.id,
                "ip_version": 4,
                "cidr":       cidr,
                "enable_dhcp": enable_dhcp,
            }

            if dns_servers:
                subnet_kwargs["dns_nameservers"] = [
                    s.strip() for s in dns_servers.split(",") if s.strip()
                ]

            conn.network.create_subnet(**subnet_kwargs)

            messages.success(
                request,
                f"Network '{name}' and its subnet created successfully.",
            )
            return redirect("network:list")

        except Exception as exc:
            messages.error(request, f"Failed to create network: {exc}")

    return render(request, "network/create_network.html")


# =========================================================
# NETWORK DETAIL
# =========================================================

def network_detail(request, network_id):
    conn = get_openstack_connection()

    try:
        network = conn.network.get_network(network_id)

        if not network:
            messages.error(request, "Network not found.")
            return redirect("network:list")

        # Subnets
        subnets = [
            conn.network.get_subnet(sid)
            for sid in (network.subnet_ids or [])
        ]

        # Ports on this network
        ports = list(conn.network.ports(network_id=network_id))

        # Instances (ports whose device_owner starts with compute)
        instance_ports = [
            p for p in ports
            if p.device_owner and p.device_owner.startswith("compute:")
        ]

        # Try to resolve instance names
        server_map = {}
        try:
            for server in conn.compute.servers():
                server_map[server.id] = server.name
        except Exception:
            pass

        return render(
            request,
            "network/network_detail.html",
            {
                "network":        network,
                "subnets":        subnets,
                "ports":          ports,
                "instance_ports": instance_ports,
                "server_map":     server_map,
            },
        )

    except Exception as exc:
        messages.error(request, f"Failed to load network details: {exc}")
        return redirect("network:list")


# =========================================================
# DELETE NETWORK
# =========================================================

def delete_network(request, network_id):
    conn = get_openstack_connection()

    try:
        network = conn.network.get_network(network_id)
        network_name = network.name if network else network_id
    except Exception:
        network_name = network_id

    if request.method == "POST":
        conn = get_openstack_connection()
        try:
            # Delete subnets first, then network
            network = conn.network.get_network(network_id)
            for sid in network.subnet_ids or []:
                try:
                    conn.network.delete_subnet(sid, ignore_missing=True)
                except Exception:
                    pass
            conn.network.delete_network(network_id, ignore_missing=True)
            messages.success(request, f"Network '{network_name}' deleted.")
            return redirect("network:list")
        except Exception as exc:
            messages.error(request, f"Failed to delete network: {exc}")
            return redirect("network:list")

    return render(
        request,
        "network/delete_network.html",
        {
            "network_id":   network_id,
            "network_name": network_name,
        },
    )


# =========================================================
# ROUTER LIST
# =========================================================

def router_list(request):
    conn = get_openstack_connection()

    try:
        routers = list(conn.network.routers())

        # Attach interface / port info per router
        for router in routers:
            try:
                router._ports = [
                    p for p in conn.network.ports(device_id=router.id)
                    if p.device_owner == "network:router_interface"
                ]
            except Exception:
                router._ports = []

    except Exception as exc:
        messages.error(request, f"Failed to load routers: {exc}")
        routers = []

    return render(request, "network/router_list.html", {"routers": routers})


# =========================================================
# CREATE ROUTER
# =========================================================

def create_router(request):
    conn = get_openstack_connection()

    # External networks for gateway selection
    try:
        external_networks = list(conn.network.networks(is_router_external=True))
        all_subnets       = list(conn.network.subnets())
        # Only subnets on non-external networks
        internal_subnets  = [
            s for s in all_subnets
            if not any(
                n.id == s.network_id
                for n in external_networks
            )
        ]
    except Exception as exc:
        messages.error(request, f"Failed to load network data: {exc}")
        external_networks = []
        internal_subnets  = []

    if request.method == "POST":
        name               = request.POST.get("name", "").strip()
        external_net_id    = request.POST.get("external_network", "").strip()
        subnet_id          = request.POST.get("subnet", "").strip()

        try:
            router_kwargs = {"name": name}

            if external_net_id:
                router_kwargs["external_gateway_info"] = {
                    "network_id": external_net_id,
                    "enable_snat": True,
                }

            router = conn.network.create_router(**router_kwargs)

            # Attach subnet interface
            if subnet_id:
                conn.network.add_interface_to_router(
                    router.id,
                    subnet_id=subnet_id,
                )

            messages.success(request, f"Router '{name}' created successfully.")
            return redirect("network:router_detail", router_id=router.id)

        except Exception as exc:
            messages.error(request, f"Failed to create router: {exc}")

    return render(
        request,
        "network/create_router.html",
        {
            "external_networks": external_networks,
            "internal_subnets":  internal_subnets,
        },
    )


# =========================================================
# ROUTER DETAIL
# =========================================================

def router_detail(request, router_id):
    conn = get_openstack_connection()

    try:
        router = conn.network.get_router(router_id)

        if not router:
            messages.error(request, "Router not found.")
            return redirect("network:router_list")

        # Ports attached to this router
        all_ports = list(conn.network.ports(device_id=router_id))

        interface_ports = [
            p for p in all_ports
            if p.device_owner == "network:router_interface"
        ]

        # Resolve subnet + network names for each interface port
        interfaces = []
        for port in interface_ports:
            for fixed_ip in port.fixed_ips:
                try:
                    subnet  = conn.network.get_subnet(fixed_ip["subnet_id"])
                    network = conn.network.get_network(subnet.network_id) if subnet else None
                    interfaces.append({
                        "port_id":      port.id,
                        "subnet_id":    fixed_ip["subnet_id"],
                        "subnet_name":  subnet.name  if subnet  else fixed_ip["subnet_id"],
                        "subnet_cidr":  subnet.cidr  if subnet  else "-",
                        "network_name": network.name if network else "-",
                        "network_id":   subnet.network_id if subnet else "-",
                        "ip_address":   fixed_ip.get("ip_address", "-"),
                    })
                except Exception:
                    interfaces.append({
                        "port_id":      port.id,
                        "subnet_id":    fixed_ip.get("subnet_id", "-"),
                        "subnet_name":  "-",
                        "subnet_cidr":  "-",
                        "network_name": "-",
                        "network_id":   "-",
                        "ip_address":   fixed_ip.get("ip_address", "-"),
                    })

        # Subnets available to add (not already attached)
        attached_subnet_ids = {iface["subnet_id"] for iface in interfaces}

        try:
            external_networks = list(conn.network.networks(is_router_external=True))
            ext_net_ids       = {n.id for n in external_networks}
            available_subnets = [
                s for s in conn.network.subnets()
                if s.id not in attached_subnet_ids
                and s.network_id not in ext_net_ids
            ]
        except Exception:
            available_subnets = []
            external_networks = []
            ext_net_ids       = set()

        return render(
            request,
            "network/router_detail.html",
            {
                "router":            router,
                "interfaces":        interfaces,
                "available_subnets": available_subnets,
                "external_networks": external_networks,
            },
        )

    except Exception as exc:
        messages.error(request, f"Failed to load router details: {exc}")
        return redirect("network:router_list")


# =========================================================
# DELETE ROUTER
# =========================================================

def delete_router(request, router_id):
    conn = get_openstack_connection()

    try:
        router      = conn.network.get_router(router_id)
        router_name = router.name if router else router_id
    except Exception:
        router_name = router_id

    if request.method == "POST":
        conn = get_openstack_connection()
        try:
            # Detach all interfaces first
            ports = list(conn.network.ports(device_id=router_id))
            for port in ports:
                if port.device_owner == "network:router_interface":
                    for fixed_ip in port.fixed_ips:
                        try:
                            conn.network.remove_interface_from_router(
                                router_id,
                                subnet_id=fixed_ip["subnet_id"],
                            )
                        except Exception:
                            pass

            conn.network.delete_router(router_id, ignore_missing=True)
            messages.success(request, f"Router '{router_name}' deleted.")
            return redirect("network:router_list")

        except Exception as exc:
            messages.error(request, f"Failed to delete router: {exc}")
            return redirect("network:router_list")

    return render(
        request,
        "network/delete_router.html",
        {
            "router_id":   router_id,
            "router_name": router_name,
        },
    )


# =========================================================
# ADD INTERFACE TO ROUTER
# =========================================================

def add_router_interface(request, router_id):
    if request.method != "POST":
        return redirect("network:router_detail", router_id=router_id)

    subnet_id = request.POST.get("subnet_id", "").strip()

    if not subnet_id:
        messages.error(request, "Please select a subnet.")
        return redirect("network:router_detail", router_id=router_id)

    conn = get_openstack_connection()

    try:
        conn.network.add_interface_to_router(router_id, subnet_id=subnet_id)
        subnet = conn.network.get_subnet(subnet_id)
        messages.success(
            request,
            f"Subnet '{subnet.name if subnet else subnet_id}' attached to router.",
        )
    except Exception as exc:
        messages.error(request, f"Failed to add interface: {exc}")

    return redirect("network:router_detail", router_id=router_id)


# =========================================================
# REMOVE INTERFACE FROM ROUTER
# =========================================================

def remove_router_interface(request, router_id):
    if request.method != "POST":
        return redirect("network:router_detail", router_id=router_id)

    subnet_id = request.POST.get("subnet_id", "").strip()

    if not subnet_id:
        messages.error(request, "No subnet specified.")
        return redirect("network:router_detail", router_id=router_id)

    conn = get_openstack_connection()

    try:
        conn.network.remove_interface_from_router(router_id, subnet_id=subnet_id)
        messages.success(request, "Interface removed from router.")
    except Exception as exc:
        messages.error(request, f"Failed to remove interface: {exc}")

    return redirect("network:router_detail", router_id=router_id)


# =========================================================
# SET / CLEAR ROUTER GATEWAY
# =========================================================

def set_router_gateway(request, router_id):
    if request.method != "POST":
        return redirect("network:router_detail", router_id=router_id)

    external_net_id = request.POST.get("external_network_id", "").strip()
    conn = get_openstack_connection()

    try:
        if external_net_id:
            conn.network.update_router(
                router_id,
                external_gateway_info={
                    "network_id":  external_net_id,
                    "enable_snat": True,
                },
            )
            messages.success(request, "Gateway set successfully.")
        else:
            conn.network.update_router(
                router_id,
                external_gateway_info={},
            )
            messages.success(request, "Gateway cleared.")
    except Exception as exc:
        messages.error(request, f"Failed to update gateway: {exc}")

    return redirect("network:router_detail", router_id=router_id)
