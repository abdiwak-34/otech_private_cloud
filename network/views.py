import re
from django.shortcuts import render, redirect
from django.contrib import messages
from dashboard.views import get_openstack_connection


# ── Helpers ───────────────────────────────────────────────

def _friendly(exc):
    """
    Convert raw OpenStack SDK / HTTP exceptions into short,
    user-readable messages without exposing URLs or stack traces.
    """
    raw = str(exc)

    # 409 Conflict — subnet already in use
    if "409" in raw or "Conflict" in raw:
        if "already allocated" in raw or "already in use" in raw or "IP address" in raw:
            return "That subnet is already attached to a router. Choose a different one."
        return "Conflict: the resource is already in use or the operation is not allowed."

    # 404 Not Found
    if "404" in raw or "not found" in raw.lower():
        return "Resource not found. It may have already been deleted."

    # 400 Bad Request
    if "400" in raw or "Bad Request" in raw:
        return "Invalid request. Check that all values (CIDR, name, etc.) are correct."

    # 403 Forbidden
    if "403" in raw or "Forbidden" in raw:
        return "Permission denied. You may not have access to perform this action."

    # Strip HTTP URLs from the message
    clean = re.sub(r'https?://\S+', '', raw).strip()
    # Strip generic SDK prefix noise
    for prefix in ("HttpException:", "ConflictException:", "NotFoundException:",
                   "BadRequestException:", "SDKException:"):
        clean = clean.replace(prefix, "").strip()
    # Truncate very long messages
    if len(clean) > 200:
        clean = clean[:200] + "…"
    return clean or "An unexpected error occurred. Please try again."


def _subnet_to_dict(s):
    return {"id": s.id, "name": s.name or "", "cidr": s.cidr or ""}


# =========================================================
# NETWORK LIST
# =========================================================

def network_list(request):
    conn = get_openstack_connection()
    try:
        raw_networks = list(conn.network.networks())
        raw_routers  = list(conn.network.routers())

        networks = []
        for net in raw_networks:
            subnets = []
            for sid in (net.subnet_ids or []):
                try:
                    s = conn.network.get_subnet(sid)
                    if s:
                        subnets.append({"id": s.id, "name": s.name or "", "cidr": s.cidr or ""})
                except Exception:
                    pass

            networks.append({
                "id":                net.id,
                "name":              net.name or "",
                "status":            net.status or "",
                "is_admin_state_up": net.is_admin_state_up,
                "is_router_external": getattr(net, "is_router_external", False),
                "is_shared":         getattr(net, "is_shared", False),
                "subnet_ids":        net.subnet_ids or [],
                "subnets":           subnets,
            })

        routers = []
        for rtr in raw_routers:
            iface_ips = []
            try:
                for port in conn.network.ports(device_id=rtr.id):
                    if port.device_owner == "network:router_interface":
                        for fip in (port.fixed_ips or []):
                            if fip.get("ip_address"):
                                iface_ips.append(fip["ip_address"])
            except Exception:
                pass

            gw_info = rtr.external_gateway_info or {}
            routers.append({
                "id":            rtr.id,
                "name":          rtr.name or "",
                "status":        rtr.status or "",
                "gw_network_id": gw_info.get("network_id", ""),
                "gw_snat":       gw_info.get("enable_snat", False),
                "iface_ips":     iface_ips,
            })

    except Exception as exc:
        messages.error(request, _friendly(exc))
        networks = []
        routers  = []

    return render(request, "network/network_list.html",
                  {"networks": networks, "routers": routers})


# =========================================================
# CREATE NETWORK
# =========================================================

def create_network(request):
    if request.method == "POST":
        name        = request.POST.get("name", "").strip()
        subnet_name = request.POST.get("subnet_name", "").strip()
        cidr        = request.POST.get("cidr", "").strip()
        dns_servers = request.POST.get("dns_servers", "").strip()
        enable_dhcp = request.POST.get("enable_dhcp") == "on"

        if not name:
            messages.error(request, "Network name is required.")
            return render(request, "network/create_network.html",
                          {"form_data": request.POST})

        if not cidr:
            messages.error(request, "Subnet CIDR is required.")
            return render(request, "network/create_network.html",
                          {"form_data": request.POST})

        conn = get_openstack_connection()
        try:
            network = conn.network.create_network(name=name)

            subnet_kwargs = {
                "name":        subnet_name or f"{name}-subnet",
                "network_id":  network.id,
                "ip_version":  4,
                "cidr":        cidr,
                "enable_dhcp": enable_dhcp,
            }
            if dns_servers:
                subnet_kwargs["dns_nameservers"] = [
                    s.strip() for s in dns_servers.split(",") if s.strip()
                ]

            conn.network.create_subnet(**subnet_kwargs)
            messages.success(request, f"Network '{name}' created successfully.")
            return redirect("network:list")

        except Exception as exc:
            # If subnet creation failed, clean up the network
            try:
                conn.network.delete_network(network.id, ignore_missing=True)
            except Exception:
                pass
            messages.error(request, _friendly(exc))
            return render(request, "network/create_network.html",
                          {"form_data": request.POST})

    return render(request, "network/create_network.html", {"form_data": {}})


# =========================================================
# NETWORK DETAIL
# =========================================================

def network_detail(request, network_id):
    conn = get_openstack_connection()
    try:
        net = conn.network.get_network(network_id)
        if not net:
            messages.error(request, "Network not found.")
            return redirect("network:list")

        network = {
            "id":                 net.id,
            "name":               net.name or "",
            "status":             net.status or "",
            "is_admin_state_up":  net.is_admin_state_up,
            "is_router_external": getattr(net, "is_router_external", False),
            "is_shared":          getattr(net, "is_shared", False),
            "subnet_ids":         net.subnet_ids or [],
        }

        subnets = []
        for sid in (net.subnet_ids or []):
            try:
                s = conn.network.get_subnet(sid)
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

        raw_ports = list(conn.network.ports(network_id=network_id))
        ports = []
        instance_ports = []
        for p in raw_ports:
            fixed_ips = [
                {"ip_address": f.get("ip_address", ""),
                 "subnet_id":  f.get("subnet_id",  "")}
                for f in (p.fixed_ips or [])
            ]
            port_dict = {
                "id":           p.id,
                "mac_address":  p.mac_address or "",
                "status":       p.status or "",
                "device_owner": p.device_owner or "",
                "device_id":    p.device_id or "",
                "fixed_ips":    fixed_ips,
            }
            ports.append(port_dict)
            if p.device_owner and p.device_owner.startswith("compute:"):
                instance_ports.append(port_dict)

        server_map = {}
        try:
            for server in conn.compute.servers():
                server_map[server.id] = server.name
        except Exception:
            pass

        return render(request, "network/network_detail.html", {
            "network":        network,
            "subnets":        subnets,
            "ports":          ports,
            "instance_ports": instance_ports,
            "server_map":     server_map,
        })

    except Exception as exc:
        messages.error(request, _friendly(exc))
        return redirect("network:list")


# =========================================================
# DELETE NETWORK
# =========================================================

def delete_network(request, network_id):
    conn = get_openstack_connection()
    try:
        net = conn.network.get_network(network_id)
        network_name = net.name if net else network_id
    except Exception:
        network_name = network_id

    if request.method == "POST":
        try:
            net = conn.network.get_network(network_id)
            for sid in (net.subnet_ids or []):
                try:
                    conn.network.delete_subnet(sid, ignore_missing=True)
                except Exception:
                    pass
            conn.network.delete_network(network_id, ignore_missing=True)
            messages.success(request, f"Network '{network_name}' deleted.")
            return redirect("network:list")
        except Exception as exc:
            messages.error(request, _friendly(exc))
            return redirect("network:list")

    return render(request, "network/delete_network.html",
                  {"network_id": network_id, "network_name": network_name})


# =========================================================
# ROUTER LIST
# =========================================================

def router_list(request):
    conn = get_openstack_connection()
    try:
        raw_routers = list(conn.network.routers())
        routers = []
        for rtr in raw_routers:
            iface_ips = []
            try:
                for port in conn.network.ports(device_id=rtr.id):
                    if port.device_owner == "network:router_interface":
                        for fip in (port.fixed_ips or []):
                            if fip.get("ip_address"):
                                iface_ips.append(fip["ip_address"])
            except Exception:
                pass

            gw_info = rtr.external_gateway_info or {}
            routers.append({
                "id":            rtr.id,
                "name":          rtr.name or "",
                "status":        rtr.status or "",
                "gw_network_id": gw_info.get("network_id", ""),
                "gw_snat":       gw_info.get("enable_snat", False),
                "iface_ips":     iface_ips,
            })

    except Exception as exc:
        messages.error(request, _friendly(exc))
        routers = []

    return render(request, "network/router_list.html", {"routers": routers})


# =========================================================
# CREATE ROUTER
# =========================================================

def create_router(request):
    conn = get_openstack_connection()

    try:
        external_networks = list(conn.network.networks(is_router_external=True))
        all_subnets       = list(conn.network.subnets())
        ext_ids           = {n.id for n in external_networks}

        # Collect subnet IDs already attached to ANY router so we can hide them
        attached_subnet_ids = set()
        try:
            for rtr in conn.network.routers():
                for port in conn.network.ports(device_id=rtr.id):
                    if port.device_owner == "network:router_interface":
                        for fip in (port.fixed_ips or []):
                            sid = fip.get("subnet_id")
                            if sid:
                                attached_subnet_ids.add(sid)
        except Exception:
            pass

        # Only show subnets that are internal AND not already attached
        internal_subnets = [
            _subnet_to_dict(s) for s in all_subnets
            if s.network_id not in ext_ids
            and s.id not in attached_subnet_ids
        ]

        ext_nets_list = [{"id": n.id, "name": n.name or ""} for n in external_networks]

    except Exception as exc:
        messages.error(request, _friendly(exc))
        internal_subnets = []
        ext_nets_list    = []

    if request.method == "POST":
        name            = request.POST.get("name", "").strip()
        external_net_id = request.POST.get("external_network", "").strip()
        subnet_id       = request.POST.get("subnet", "").strip()

        if not name:
            messages.error(request, "Router name is required.")
            return render(request, "network/create_router.html", {
                "external_networks": ext_nets_list,
                "internal_subnets":  internal_subnets,
                "form_data":         request.POST,
            })

        try:
            router_kwargs = {"name": name}
            if external_net_id:
                router_kwargs["external_gateway_info"] = {
                    "network_id":  external_net_id,
                    "enable_snat": True,
                }

            router = conn.network.create_router(**router_kwargs)

            if subnet_id:
                try:
                    conn.network.add_interface_to_router(router.id, subnet_id=subnet_id)
                except Exception as exc:
                    messages.warning(request,
                        f"Router created but could not attach subnet: {_friendly(exc)}")
                    return redirect("network:router_detail", router_id=router.id)

            messages.success(request, f"Router '{name}' created successfully.")
            return redirect("network:router_detail", router_id=router.id)

        except Exception as exc:
            messages.error(request, _friendly(exc))

    return render(request, "network/create_router.html", {
        "external_networks": ext_nets_list,
        "internal_subnets":  internal_subnets,
        "form_data":         request.POST if request.method == "POST" else {},
    })


# =========================================================
# ROUTER DETAIL
# =========================================================

def router_detail(request, router_id):
    conn = get_openstack_connection()
    try:
        rtr = conn.network.get_router(router_id)
        if not rtr:
            messages.error(request, "Router not found.")
            return redirect("network:router_list")

        gw_info = rtr.external_gateway_info or {}
        router = {
            "id":               rtr.id,
            "name":             rtr.name or "",
            "status":           rtr.status or "",
            "is_admin_state_up": rtr.is_admin_state_up,
            "gw_network_id":    gw_info.get("network_id", ""),
            "gw_snat":          gw_info.get("enable_snat", False),
        }

        all_ports = list(conn.network.ports(device_id=router_id))
        interface_ports = [p for p in all_ports
                           if p.device_owner == "network:router_interface"]

        interfaces = []
        attached_subnet_ids = set()
        for port in interface_ports:
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

        try:
            external_networks = list(conn.network.networks(is_router_external=True))
            ext_net_ids       = {n.id for n in external_networks}
            # Available = internal + not already attached to THIS router
            available_subnets = [
                _subnet_to_dict(s)
                for s in conn.network.subnets()
                if s.id not in attached_subnet_ids
                and s.network_id not in ext_net_ids
            ]
            ext_nets_list = [{"id": n.id, "name": n.name or ""} for n in external_networks]
        except Exception:
            available_subnets = []
            ext_nets_list     = []

        return render(request, "network/router_detail.html", {
            "router":            router,
            "interfaces":        interfaces,
            "available_subnets": available_subnets,
            "external_networks": ext_nets_list,
        })

    except Exception as exc:
        messages.error(request, _friendly(exc))
        return redirect("network:router_list")


# =========================================================
# DELETE ROUTER
# =========================================================

def delete_router(request, router_id):
    conn = get_openstack_connection()
    try:
        rtr         = conn.network.get_router(router_id)
        router_name = rtr.name if rtr else router_id
    except Exception:
        router_name = router_id

    if request.method == "POST":
        try:
            for port in conn.network.ports(device_id=router_id):
                if port.device_owner == "network:router_interface":
                    for fip in (port.fixed_ips or []):
                        try:
                            conn.network.remove_interface_from_router(
                                router_id, subnet_id=fip["subnet_id"])
                        except Exception:
                            pass
            conn.network.delete_router(router_id, ignore_missing=True)
            messages.success(request, f"Router '{router_name}' deleted.")
            return redirect("network:router_list")
        except Exception as exc:
            messages.error(request, _friendly(exc))
            return redirect("network:router_list")

    return render(request, "network/delete_router.html",
                  {"router_id": router_id, "router_name": router_name})


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
        name   = subnet.name if subnet else subnet_id
        messages.success(request, f"Subnet '{name}' attached to router.")
    except Exception as exc:
        messages.error(request, _friendly(exc))

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
        messages.error(request, _friendly(exc))

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
                external_gateway_info={"network_id": external_net_id, "enable_snat": True},
            )
            messages.success(request, "External gateway updated.")
        else:
            conn.network.update_router(router_id, external_gateway_info={})
            messages.success(request, "External gateway cleared.")
    except Exception as exc:
        messages.error(request, _friendly(exc))

    return redirect("network:router_detail", router_id=router_id)
