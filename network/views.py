from django.contrib import messages
from django.shortcuts import render, redirect
from dashboard.views import get_openstack_connection


# -------------------------------------------------------
# Error message helper
# OpenStack errors include raw HTTP URLs and long stack
# traces. This converts them to short, readable sentences.
# -------------------------------------------------------

def friendly_error(exc):
    text = str(exc)
    if "409" in text or "Conflict" in text:
        if "already allocated" in text or "already in use" in text or "IP address" in text:
            return "That subnet is already attached to a router. Pick a different one."
        return "This operation conflicts with the current state of the resource."
    if "404" in text or "not found" in text.lower():
        return "Resource not found. It may have already been deleted."
    if "400" in text or "Bad Request" in text:
        return "Invalid input. Check the network name, CIDR, and other fields."
    if "403" in text or "Forbidden" in text:
        return "You do not have permission to do that."
    # Fall back to the raw message but strip HTTP URLs to keep it clean
    import re
    clean = re.sub(r"https?://\S+", "", text).strip()
    return clean[:200] if len(clean) > 200 else clean or "An unexpected error occurred."


# -------------------------------------------------------
# NETWORK LIST
# Shows all networks (with their subnets) and all routers
# on a single overview page.
# -------------------------------------------------------

def network_list(request):
    conn = get_openstack_connection()

    try:
        # Build network list with subnets included
        networks = []
        for net in conn.network.networks():
            subnets = []
            for subnet_id in (net.subnet_ids or []):
                try:
                    s = conn.network.get_subnet(subnet_id)
                    if s:
                        subnets.append({"id": s.id, "name": s.name or "", "cidr": s.cidr or ""})
                except Exception:
                    pass  # Skip if a subnet can't be fetched

            # Convert SDK object to a plain dict so the template
            # doesn't hit any underscore attributes (Django blocks those)
            networks.append({
                "id":                 net.id,
                "name":               net.name or "",
                "status":             net.status or "",
                "is_admin_state_up":  net.is_admin_state_up,
                "is_router_external": getattr(net, "is_router_external", False),
                "is_shared":          getattr(net, "is_shared", False),
                "subnet_ids":         net.subnet_ids or [],
                "subnets":            subnets,
            })

        # Build router list with interface IPs included
        routers = []
        for rtr in conn.network.routers():
            # Collect the IPs of all internal interfaces
            interface_ips = []
            for port in conn.network.ports(device_id=rtr.id):
                if port.device_owner == "network:router_interface":
                    for fixed_ip in (port.fixed_ips or []):
                        if fixed_ip.get("ip_address"):
                            interface_ips.append(fixed_ip["ip_address"])

            gateway = rtr.external_gateway_info or {}
            routers.append({
                "id":            rtr.id,
                "name":          rtr.name or "",
                "status":        rtr.status or "",
                "gw_network_id": gateway.get("network_id", ""),
                "gw_snat":       gateway.get("enable_snat", False),
                "iface_ips":     interface_ips,
            })

    except Exception as e:
        messages.error(request, friendly_error(e))
        networks = []
        routers  = []

    return render(request, "network/network_list.html", {
        "networks": networks,
        "routers":  routers,
    })


# -------------------------------------------------------
# CREATE NETWORK
# Only creates the Neutron network (just a name).
# After creation the user is taken to the subnet creation
# page to add an IP range to the network.
# -------------------------------------------------------

def create_network(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()

        if not name:
            messages.error(request, "Network name is required.")
            return render(request, "network/create_network.html", {"form_data": request.POST})

        conn = get_openstack_connection()
        try:
            network = conn.network.create_network(name=name)
            messages.success(request, f"Network '{name}' created. Now add a subnet to it.")
            # Redirect to the subnet creation page for this network
            return redirect("network:create_subnet", network_id=network.id)

        except Exception as e:
            messages.error(request, friendly_error(e))
            return render(request, "network/create_network.html", {"form_data": request.POST})

    # GET: show the simple name-only form
    return render(request, "network/create_network.html", {"form_data": {}})


# -------------------------------------------------------
# CREATE SUBNET
# Adds a subnet (IP range) to an existing network.
# This is reached automatically after creating a network,
# but the user can also come here from the network detail
# page to add more subnets later.
# -------------------------------------------------------

def create_subnet(request, network_id):
    conn = get_openstack_connection()

    # Fetch the parent network so we can show its name
    try:
        net = conn.network.get_network(network_id)
        if not net:
            messages.error(request, "Network not found.")
            return redirect("network:list")
        network_name = net.name or network_id
    except Exception as e:
        messages.error(request, friendly_error(e))
        return redirect("network:list")

    if request.method == "POST":
        subnet_name = request.POST.get("subnet_name", "").strip()
        cidr        = request.POST.get("cidr", "").strip()
        dns_servers = request.POST.get("dns_servers", "").strip()
        enable_dhcp = request.POST.get("enable_dhcp") == "on"

        if not cidr:
            messages.error(request, "IP range (CIDR) is required, e.g. 192.168.100.0/24")
            return render(request, "network/create_subnet.html", {
                "network_id":   network_id,
                "network_name": network_name,
                "form_data":    request.POST,
            })

        try:
            subnet_kwargs = {
                "name":        subnet_name or f"{network_name}-subnet",
                "network_id":  network_id,
                "ip_version":  4,
                "cidr":        cidr,
                "enable_dhcp": enable_dhcp,
            }
            # Add DNS servers if the user provided any
            if dns_servers:
                subnet_kwargs["dns_nameservers"] = [
                    ip.strip() for ip in dns_servers.split(",") if ip.strip()
                ]

            conn.network.create_subnet(**subnet_kwargs)
            messages.success(request, f"Subnet '{cidr}' added to network '{network_name}'.")
            return redirect("network:detail", network_id=network_id)

        except Exception as e:
            messages.error(request, friendly_error(e))
            return render(request, "network/create_subnet.html", {
                "network_id":   network_id,
                "network_name": network_name,
                "form_data":    request.POST,
            })

    # GET: show the subnet form
    return render(request, "network/create_subnet.html", {
        "network_id":   network_id,
        "network_name": network_name,
        "form_data":    {},
    })


# -------------------------------------------------------
# NETWORK DETAIL
# Shows the subnets, ports, and instances connected to
# a specific network.
# -------------------------------------------------------

def network_detail(request, network_id):
    conn = get_openstack_connection()

    try:
        net = conn.network.get_network(network_id)
        if not net:
            messages.error(request, "Network not found.")
            return redirect("network:list")

        # Plain dict version of the network for the template
        network = {
            "id":                 net.id,
            "name":               net.name or "",
            "status":             net.status or "",
            "is_admin_state_up":  net.is_admin_state_up,
            "is_router_external": getattr(net, "is_router_external", False),
            "is_shared":          getattr(net, "is_shared", False),
            "subnet_ids":         net.subnet_ids or [],
        }

        # Fetch full subnet details
        subnets = []
        for subnet_id in (net.subnet_ids or []):
            try:
                s = conn.network.get_subnet(subnet_id)
                if s:
                    subnets.append({
                        "id":              s.id,
                        "name":            s.name or "",
                        "cidr":            s.cidr or "",
                        "gateway_ip":      s.gateway_ip or "",
                        "ip_version":      s.ip_version,
                        "is_dhcp_enabled": s.is_dhcp_enabled,
                    })
            except Exception:
                pass

        # Fetch all ports on this network
        ports = []
        instance_ports = []
        for p in conn.network.ports(network_id=network_id):
            fixed_ips = [
                {"ip_address": f.get("ip_address", ""), "subnet_id": f.get("subnet_id", "")}
                for f in (p.fixed_ips or [])
            ]
            port = {
                "id":           p.id,
                "mac_address":  p.mac_address or "",
                "status":       p.status or "",
                "device_owner": p.device_owner or "",
                "device_id":    p.device_id or "",
                "fixed_ips":    fixed_ips,
            }
            ports.append(port)
            # Ports owned by "compute:..." belong to VM instances
            if p.device_owner and p.device_owner.startswith("compute:"):
                instance_ports.append(port)

        # Build a map of instance_id -> instance_name for display
        server_map = {}
        for server in conn.compute.servers():
            server_map[server.id] = server.name

        return render(request, "network/network_detail.html", {
            "network":        network,
            "subnets":        subnets,
            "ports":          ports,
            "instance_ports": instance_ports,
            "server_map":     server_map,
        })

    except Exception as e:
        messages.error(request, friendly_error(e))
        return redirect("network:list")


# -------------------------------------------------------
# DELETE NETWORK
# Shows a confirmation page. On confirm, deletes all
# subnets first (required by Neutron) then the network.
# -------------------------------------------------------

def delete_network(request, network_id):
    conn = get_openstack_connection()

    # Fetch the name now so the confirmation page can display it
    try:
        net = conn.network.get_network(network_id)
        network_name = net.name if net else network_id
    except Exception:
        network_name = network_id

    if request.method == "POST":
        try:
            net = conn.network.get_network(network_id)
            # Neutron requires all subnets to be deleted before the network
            for subnet_id in (net.subnet_ids or []):
                conn.network.delete_subnet(subnet_id, ignore_missing=True)
            conn.network.delete_network(network_id, ignore_missing=True)
            messages.success(request, f"Network '{network_name}' deleted.")
            return redirect("network:list")
        except Exception as e:
            messages.error(request, friendly_error(e))
            return redirect("network:list")

    # GET: show the confirmation page
    return render(request, "network/delete_network.html", {
        "network_id":   network_id,
        "network_name": network_name,
    })


# -------------------------------------------------------
# ROUTER LIST
# Shows all Neutron routers with their gateway and
# interface IP addresses.
# -------------------------------------------------------

def router_list(request):
    conn = get_openstack_connection()

    try:
        routers = []
        for rtr in conn.network.routers():
            # Collect IPs of all internal router interfaces
            interface_ips = []
            for port in conn.network.ports(device_id=rtr.id):
                if port.device_owner == "network:router_interface":
                    for fixed_ip in (port.fixed_ips or []):
                        if fixed_ip.get("ip_address"):
                            interface_ips.append(fixed_ip["ip_address"])

            gateway = rtr.external_gateway_info or {}
            routers.append({
                "id":            rtr.id,
                "name":          rtr.name or "",
                "status":        rtr.status or "",
                "gw_network_id": gateway.get("network_id", ""),
                "gw_snat":       gateway.get("enable_snat", False),
                "iface_ips":     interface_ips,
            })

    except Exception as e:
        messages.error(request, friendly_error(e))
        routers = []

    return render(request, "network/router_list.html", {"routers": routers})


# -------------------------------------------------------
# CREATE ROUTER
# Creates a Neutron router. Optionally connects it to
# an external network (gateway) and attaches a private
# subnet as its first interface.
# -------------------------------------------------------

def create_router(request):
    conn = get_openstack_connection()

    try:
        # External networks are provider networks with internet access
        external_networks = list(conn.network.networks(is_router_external=True))
        ext_ids = {n.id for n in external_networks}

        # Find which subnets are already attached to any router
        # so we can hide them from the dropdown (can't attach twice)
        already_attached = set()
        for rtr in conn.network.routers():
            for port in conn.network.ports(device_id=rtr.id):
                if port.device_owner == "network:router_interface":
                    for fixed_ip in (port.fixed_ips or []):
                        if fixed_ip.get("subnet_id"):
                            already_attached.add(fixed_ip["subnet_id"])

        # Only show subnets that are private and not yet attached
        available_subnets = [
            {"id": s.id, "name": s.name or "", "cidr": s.cidr or ""}
            for s in conn.network.subnets()
            if s.network_id not in ext_ids and s.id not in already_attached
        ]

        ext_nets = [{"id": n.id, "name": n.name or ""} for n in external_networks]

    except Exception as e:
        messages.error(request, friendly_error(e))
        available_subnets = []
        ext_nets = []

    if request.method == "POST":
        name            = request.POST.get("name", "").strip()
        external_net_id = request.POST.get("external_network", "").strip()
        subnet_id       = request.POST.get("subnet", "").strip()

        if not name:
            messages.error(request, "Router name is required.")
            return render(request, "network/create_router.html", {
                "external_networks": ext_nets,
                "internal_subnets":  available_subnets,
                "form_data":         request.POST,
            })

        try:
            # Build the router — optionally with an external gateway
            router_config = {"name": name}
            if external_net_id:
                router_config["external_gateway_info"] = {
                    "network_id":  external_net_id,
                    "enable_snat": True,
                }

            router = conn.network.create_router(**router_config)

            # Optionally attach a private subnet as an interface
            if subnet_id:
                conn.network.add_interface_to_router(router.id, subnet_id=subnet_id)

            messages.success(request, f"Router '{name}' created.")
            return redirect("network:router_detail", router_id=router.id)

        except Exception as e:
            messages.error(request, friendly_error(e))

    # GET: show the empty form
    return render(request, "network/create_router.html", {
        "external_networks": ext_nets,
        "internal_subnets":  available_subnets,
        "form_data":         request.POST if request.method == "POST" else {},
    })


# -------------------------------------------------------
# ROUTER DETAIL
# Shows the router's current interfaces (attached subnets)
# and lets the user add/remove interfaces or change the
# external gateway.
# -------------------------------------------------------

def router_detail(request, router_id):
    conn = get_openstack_connection()

    try:
        rtr = conn.network.get_router(router_id)
        if not rtr:
            messages.error(request, "Router not found.")
            return redirect("network:router_list")

        gateway = rtr.external_gateway_info or {}

        # Plain dict for the template
        router = {
            "id":               rtr.id,
            "name":             rtr.name or "",
            "status":           rtr.status or "",
            "is_admin_state_up": rtr.is_admin_state_up,
            "gw_network_id":    gateway.get("network_id", ""),
            "gw_snat":          gateway.get("enable_snat", False),
        }

        # Build the list of interfaces (subnets attached to this router)
        interfaces = []
        attached_subnet_ids = set()

        for port in conn.network.ports(device_id=router_id):
            if port.device_owner != "network:router_interface":
                continue
            for fixed_ip in (port.fixed_ips or []):
                sid = fixed_ip["subnet_id"]
                attached_subnet_ids.add(sid)
                try:
                    subnet  = conn.network.get_subnet(sid)
                    network = conn.network.get_network(subnet.network_id) if subnet else None
                    interfaces.append({
                        "port_id":      port.id,
                        "subnet_id":    sid,
                        "subnet_name":  subnet.name if subnet else sid,
                        "subnet_cidr":  subnet.cidr if subnet else "-",
                        "network_name": network.name if network else "-",
                        "ip_address":   fixed_ip.get("ip_address", "-"),
                    })
                except Exception:
                    interfaces.append({
                        "port_id":      port.id,
                        "subnet_id":    sid,
                        "subnet_name":  sid[:16] + "…",
                        "subnet_cidr":  "-",
                        "network_name": "-",
                        "ip_address":   fixed_ip.get("ip_address", "-"),
                    })

        # Subnets available to add (private, not already attached)
        external_networks = list(conn.network.networks(is_router_external=True))
        ext_ids = {n.id for n in external_networks}

        available_subnets = [
            {"id": s.id, "name": s.name or "", "cidr": s.cidr or ""}
            for s in conn.network.subnets()
            if s.id not in attached_subnet_ids and s.network_id not in ext_ids
        ]

        ext_nets = [{"id": n.id, "name": n.name or ""} for n in external_networks]

        return render(request, "network/router_detail.html", {
            "router":            router,
            "interfaces":        interfaces,
            "available_subnets": available_subnets,
            "external_networks": ext_nets,
        })

    except Exception as e:
        messages.error(request, friendly_error(e))
        return redirect("network:router_list")


# -------------------------------------------------------
# DELETE ROUTER
# Detaches all subnet interfaces first (Neutron requires
# this), then deletes the router.
# -------------------------------------------------------

def delete_router(request, router_id):
    conn = get_openstack_connection()

    try:
        rtr = conn.network.get_router(router_id)
        router_name = rtr.name if rtr else router_id
    except Exception:
        router_name = router_id

    if request.method == "POST":
        try:
            # Remove all subnet interfaces before deleting
            for port in conn.network.ports(device_id=router_id):
                if port.device_owner == "network:router_interface":
                    for fixed_ip in (port.fixed_ips or []):
                        conn.network.remove_interface_from_router(
                            router_id, subnet_id=fixed_ip["subnet_id"]
                        )
            conn.network.delete_router(router_id, ignore_missing=True)
            messages.success(request, f"Router '{router_name}' deleted.")
            return redirect("network:router_list")
        except Exception as e:
            messages.error(request, friendly_error(e))
            return redirect("network:router_list")

    # GET: show the confirmation page
    return render(request, "network/delete_router.html", {
        "router_id":   router_id,
        "router_name": router_name,
    })


# -------------------------------------------------------
# ADD INTERFACE TO ROUTER
# Connects a private subnet to the router. This allows
# instances on that subnet to route through the router.
# -------------------------------------------------------

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
        messages.success(request, f"Subnet '{subnet.name if subnet else subnet_id}' attached to router.")
    except Exception as e:
        messages.error(request, friendly_error(e))

    return redirect("network:router_detail", router_id=router_id)


# -------------------------------------------------------
# REMOVE INTERFACE FROM ROUTER
# Disconnects a private subnet from the router.
# -------------------------------------------------------

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
        messages.success(request, "Interface removed.")
    except Exception as e:
        messages.error(request, friendly_error(e))

    return redirect("network:router_detail", router_id=router_id)


# -------------------------------------------------------
# SET / CLEAR ROUTER GATEWAY
# Links the router to an external network so traffic from
# internal subnets can reach the internet via SNAT.
# Sending an empty network ID clears the gateway.
# -------------------------------------------------------

def set_router_gateway(request, router_id):
    if request.method != "POST":
        return redirect("network:router_detail", router_id=router_id)

    external_net_id = request.POST.get("external_network_id", "").strip()
    conn = get_openstack_connection()

    try:
        if external_net_id:
            conn.network.update_router(
                router_id,
                external_gateway_info={"network_id": external_net_id, "enable_snat": True},
            )
            messages.success(request, "External gateway updated.")
        else:
            conn.network.update_router(router_id, external_gateway_info={})
            messages.success(request, "External gateway cleared.")
    except Exception as e:
        messages.error(request, friendly_error(e))

    return redirect("network:router_detail", router_id=router_id)
