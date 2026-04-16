from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn
import threading
from fastmcp import FastMCP
import httpx
import os
from typing import Optional

mcp = FastMCP("go-proxmox")

# In-memory session store for Proxmox connections
_sessions: dict = {}
_mock_environments: dict = {}


def _get_session(uri: str) -> Optional[dict]:
    """Retrieve stored session credentials for a URI."""
    return _sessions.get(uri)


async def _proxmox_request(
    method: str,
    uri: str,
    path: str,
    verify_ssl: bool = True,
    ticket: str = None,
    csrf_token: str = None,
    **kwargs
) -> dict:
    """Make an authenticated request to the Proxmox API."""
    url = f"{uri.rstrip('/')}/api2/json{path}"
    headers = {}
    cookies = {}

    if ticket:
        cookies["PVEAuthCookie"] = ticket
    if csrf_token:
        headers["CSRFPreventionToken"] = csrf_token

    async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            cookies=cookies,
            **kwargs
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def connect_proxmox(
    uri: str,
    username: str,
    password: str,
    tls_insecure: bool = False
) -> dict:
    """
    Initialize and configure a connection to a Proxmox VE server.
    Use this first before any other operations to set up the client
    with the server URI and authentication credentials.
    Supports Proxmox VE versions 6.x through 9.x.
    """
    try:
        url = f"{uri.rstrip('/')}/api2/json/access/ticket"
        async with httpx.AsyncClient(verify=not tls_insecure, timeout=30.0) as client:
            response = await client.post(
                url,
                data={"username": username, "password": password}
            )
            response.raise_for_status()
            data = response.json()

        ticket = data.get("data", {}).get("ticket")
        csrf_token = data.get("data", {}).get("CSRFPreventionToken")
        cap = data.get("data", {}).get("cap", {})

        if not ticket:
            return {"success": False, "error": "Authentication failed: no ticket received"}

        # Store session
        _sessions[uri] = {
            "uri": uri,
            "username": username,
            "ticket": ticket,
            "csrf_token": csrf_token,
            "tls_insecure": tls_insecure,
            "capabilities": cap
        }

        return {
            "success": True,
            "uri": uri,
            "username": username,
            "message": f"Successfully connected to Proxmox VE at {uri}",
            "capabilities": cap
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.text}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_cluster_status(uri: str) -> dict:
    """
    Retrieve the current status and information about the Proxmox VE cluster,
    including nodes, quorum state, and cluster name.
    Use this to get a high-level overview of the cluster health and topology.
    """
    session = _get_session(uri)
    if not session:
        return {"success": False, "error": f"No active session for {uri}. Please call connect_proxmox first."}

    try:
        result = await _proxmox_request(
            "GET",
            uri,
            "/cluster/status",
            verify_ssl=not session["tls_insecure"],
            ticket=session["ticket"],
            csrf_token=session["csrf_token"]
        )
        return {"success": True, "data": result.get("data", [])}
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.text}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def list_nodes(uri: str) -> dict:
    """
    List all nodes in the Proxmox VE cluster with their status, CPU, memory,
    and uptime information. Use this to get an inventory of all available compute nodes.
    """
    session = _get_session(uri)
    if not session:
        return {"success": False, "error": f"No active session for {uri}. Please call connect_proxmox first."}

    try:
        result = await _proxmox_request(
            "GET",
            uri,
            "/nodes",
            verify_ssl=not session["tls_insecure"],
            ticket=session["ticket"],
            csrf_token=session["csrf_token"]
        )
        nodes = result.get("data", [])
        return {
            "success": True,
            "count": len(nodes),
            "nodes": nodes
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.text}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def manage_virtual_machine(
    uri: str,
    node: str,
    vmid: int,
    action: str
) -> dict:
    """
    Perform lifecycle operations on a Proxmox VE virtual machine (VM)
    such as start, stop, reboot, suspend, or get status.
    Use this to control VM state on a specific node.
    Valid actions: 'start', 'stop', 'reboot', 'suspend', 'resume', 'status'.
    """
    session = _get_session(uri)
    if not session:
        return {"success": False, "error": f"No active session for {uri}. Please call connect_proxmox first."}

    valid_actions = {"start", "stop", "reboot", "suspend", "resume", "status"}
    if action not in valid_actions:
        return {
            "success": False,
            "error": f"Invalid action '{action}'. Must be one of: {', '.join(sorted(valid_actions))}"
        }

    try:
        verify_ssl = not session["tls_insecure"]
        ticket = session["ticket"]
        csrf_token = session["csrf_token"]

        if action == "status":
            result = await _proxmox_request(
                "GET",
                uri,
                f"/nodes/{node}/qemu/{vmid}/status/current",
                verify_ssl=verify_ssl,
                ticket=ticket,
                csrf_token=csrf_token
            )
            return {"success": True, "vmid": vmid, "node": node, "data": result.get("data", {})}
        else:
            result = await _proxmox_request(
                "POST",
                uri,
                f"/nodes/{node}/qemu/{vmid}/status/{action}",
                verify_ssl=verify_ssl,
                ticket=ticket,
                csrf_token=csrf_token
            )
            return {
                "success": True,
                "vmid": vmid,
                "node": node,
                "action": action,
                "task": result.get("data"),
                "message": f"Action '{action}' initiated for VM {vmid} on node {node}"
            }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.text}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_vnc_console(
    uri: str,
    node: str,
    vmid: int,
    type: str = "vm"
) -> dict:
    """
    Retrieve a VNC ticket and connection details for accessing the graphical
    console of a Proxmox VE virtual machine or container.
    Use this when a user needs browser-based or VNC client access to a VM's display.
    Type can be 'vm' for virtual machines or 'lxc' for containers.
    """
    session = _get_session(uri)
    if not session:
        return {"success": False, "error": f"No active session for {uri}. Please call connect_proxmox first."}

    if type not in {"vm", "lxc"}:
        return {"success": False, "error": "type must be 'vm' or 'lxc'"}

    try:
        verify_ssl = not session["tls_insecure"]
        ticket = session["ticket"]
        csrf_token = session["csrf_token"]

        if type == "vm":
            path = f"/nodes/{node}/qemu/{vmid}/vncproxy"
        else:
            path = f"/nodes/{node}/lxc/{vmid}/vncproxy"

        result = await _proxmox_request(
            "POST",
            uri,
            path,
            verify_ssl=verify_ssl,
            ticket=ticket,
            csrf_token=csrf_token,
            data={"websocket": 1}
        )
        data = result.get("data", {})
        return {
            "success": True,
            "vmid": vmid,
            "node": node,
            "type": type,
            "ticket": data.get("ticket"),
            "port": data.get("port"),
            "cert": data.get("cert"),
            "upid": data.get("upid"),
            "user": data.get("user"),
            "vnc_url": f"{uri}/api2/json/nodes/{node}/{'qemu' if type == 'vm' else 'lxc'}/{vmid}/vncwebsocket",
            "message": f"VNC ticket obtained for {'VM' if type == 'vm' else 'container'} {vmid} on node {node}"
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.text}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_terminal_console(
    uri: str,
    node: str,
    vmid: int,
    type: str = "vm"
) -> dict:
    """
    Establish a terminal (xterm/shell) WebSocket session for a Proxmox VE
    virtual machine or container. Use this when the user needs CLI/shell access
    to a VM or container via the Proxmox terminal proxy.
    Type can be 'vm' for virtual machines or 'lxc' for containers.
    """
    session = _get_session(uri)
    if not session:
        return {"success": False, "error": f"No active session for {uri}. Please call connect_proxmox first."}

    if type not in {"vm", "lxc"}:
        return {"success": False, "error": "type must be 'vm' or 'lxc'"}

    try:
        verify_ssl = not session["tls_insecure"]
        ticket = session["ticket"]
        csrf_token = session["csrf_token"]

        if type == "vm":
            path = f"/nodes/{node}/qemu/{vmid}/termproxy"
        else:
            path = f"/nodes/{node}/lxc/{vmid}/termproxy"

        result = await _proxmox_request(
            "POST",
            uri,
            path,
            verify_ssl=verify_ssl,
            ticket=ticket,
            csrf_token=csrf_token
        )
        data = result.get("data", {})
        return {
            "success": True,
            "vmid": vmid,
            "node": node,
            "type": type,
            "ticket": data.get("ticket"),
            "port": data.get("port"),
            "upid": data.get("upid"),
            "user": data.get("user"),
            "terminal_url": f"{uri}/api2/json/nodes/{node}/{'qemu' if type == 'vm' else 'lxc'}/{vmid}/vncwebsocket",
            "message": f"Terminal session established for {'VM' if type == 'vm' else 'container'} {vmid} on node {node}"
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.text}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def setup_mock_environment(
    uri: str,
    version: str = "9x",
    enabled: bool = True
) -> dict:
    """
    Configure the mock HTTP interceptor for testing purposes against a specific
    Proxmox VE version. Use this in test/development scenarios to simulate
    Proxmox API responses without a real server.
    Supports versions: '6x', '7x', '8x', '9x'.
    """
    valid_versions = {"6x", "7x", "8x", "9x"}
    if version not in valid_versions:
        return {
            "success": False,
            "error": f"Invalid version '{version}'. Must be one of: {', '.join(sorted(valid_versions))}"
        }

    if not enabled:
        if uri in _mock_environments:
            del _mock_environments[uri]
        return {
            "success": True,
            "uri": uri,
            "enabled": False,
            "message": f"Mock environment disabled for {uri}"
        }

    # Store mock environment configuration
    _mock_environments[uri] = {
        "uri": uri,
        "version": version,
        "enabled": True
    }

    # Simulate a mock session so other tools can be used
    _sessions[uri] = {
        "uri": uri,
        "username": "mock@pam",
        "ticket": f"PVE:mock@pam:MOCK_TICKET_{version.upper()}",
        "csrf_token": f"MOCK_CSRF_TOKEN_{version.upper()}",
        "tls_insecure": True,
        "capabilities": {
            "dc": {"Sys.PowerMgmt": 1, "Sys.Audit": 1},
            "nodes": {"pve": {"Sys.Audit": 1}}
        },
        "mock": True
    }

    version_map = {
        "6x": "6.x",
        "7x": "7.x",
        "8x": "8.x",
        "9x": "9.x"
    }

    return {
        "success": True,
        "uri": uri,
        "version": version,
        "proxmox_version": version_map[version],
        "enabled": True,
        "message": f"Mock environment configured for Proxmox VE {version_map[version]} at {uri}",
        "note": "This MCP server provides a simulation layer. For full mock interception (gock), use the go-proxmox package directly in Go test code.",
        "mock_session": {
            "username": "mock@pam",
            "ticket": f"PVE:mock@pam:MOCK_TICKET_{version.upper()}",
            "csrf_token": f"MOCK_CSRF_TOKEN_{version.upper()}"
        }
    }


@mcp.tool()
async def list_virtual_machines(
    uri: str,
    node: Optional[str] = None,
    resource_type: str = "all"
) -> dict:
    """
    List all virtual machines and/or LXC containers on a specific node or
    across the entire cluster. Use this to get an inventory of workloads
    including their status, resource usage, and configuration.
    resource_type can be 'vm', 'lxc', or 'all'.
    """
    session = _get_session(uri)
    if not session:
        return {"success": False, "error": f"No active session for {uri}. Please call connect_proxmox first."}

    if resource_type not in {"vm", "lxc", "all"}:
        return {
            "success": False,
            "error": "resource_type must be 'vm', 'lxc', or 'all'"
        }

    try:
        verify_ssl = not session["tls_insecure"]
        ticket = session["ticket"]
        csrf_token = session["csrf_token"]

        results = {"success": True, "vms": [], "containers": []}

        if node:
            nodes_to_query = [node]
        else:
            # Fetch all nodes first
            nodes_result = await _proxmox_request(
                "GET", uri, "/nodes",
                verify_ssl=verify_ssl, ticket=ticket, csrf_token=csrf_token
            )
            nodes_to_query = [n["node"] for n in nodes_result.get("data", [])]

        for n in nodes_to_query:
            # Fetch QEMU VMs
            if resource_type in {"vm", "all"}:
                try:
                    vm_result = await _proxmox_request(
                        "GET", uri, f"/nodes/{n}/qemu",
                        verify_ssl=verify_ssl, ticket=ticket, csrf_token=csrf_token
                    )
                    for vm in vm_result.get("data", []):
                        vm["node"] = n
                        vm["type"] = "qemu"
                    results["vms"].extend(vm_result.get("data", []))
                except Exception:
                    pass  # Node may not have QEMU VMs or may be offline

            # Fetch LXC containers
            if resource_type in {"lxc", "all"}:
                try:
                    lxc_result = await _proxmox_request(
                        "GET", uri, f"/nodes/{n}/lxc",
                        verify_ssl=verify_ssl, ticket=ticket, csrf_token=csrf_token
                    )
                    for ct in lxc_result.get("data", []):
                        ct["node"] = n
                        ct["type"] = "lxc"
                    results["containers"].extend(lxc_result.get("data", []))
                except Exception:
                    pass  # Node may not have containers or may be offline

        results["total_vms"] = len(results["vms"])
        results["total_containers"] = len(results["containers"])
        results["total"] = results["total_vms"] + results["total_containers"]
        results["node_filter"] = node or "all nodes"
        results["resource_type"] = resource_type

        return results

    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.text}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}




_SERVER_SLUG = "luthermonson-go-proxmox"

def _track(tool_name: str, ua: str = ""):
    try:
        import urllib.request, json as _json
        data = _json.dumps({"slug": _SERVER_SLUG, "event": "tool_call", "tool": tool_name, "user_agent": ua}).encode()
        req = urllib.request.Request("https://www.volspan.dev/api/analytics/event", data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass

async def health(request):
    return JSONResponse({"status": "ok", "server": mcp.name})

async def tools(request):
    registered = await mcp.list_tools()
    tool_list = [{"name": t.name, "description": t.description or ""} for t in registered]
    return JSONResponse({"tools": tool_list, "count": len(tool_list)})

sse_app = mcp.http_app(transport="sse")

app = Starlette(
    routes=[
        Route("/health", health),
        Route("/tools", tools),
        Mount("/", sse_app),
    ],
    lifespan=sse_app.lifespan,
)
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
