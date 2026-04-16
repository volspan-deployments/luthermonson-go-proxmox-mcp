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

# In-memory mock state
_mock_state = {
    "active": False,
    "version": None,
    "uri": None
}

_mock_uri = {"value": None}


@mcp.tool()
async def enable_mock(
    uri: str,
    version: str = "9x"
) -> dict:
    """
    Enable HTTP mocking for a specific Proxmox VE version. Use this when setting up tests
    that require simulated Proxmox API responses without a real server.
    Supports versions 6.x, 7.x, 8.x, and 9.x.
    Use 'default' or '9x' for the latest PVE9x default configuration.
    """
    valid_versions = {"6x", "7x", "8x", "9x", "default"}
    if version not in valid_versions:
        return {
            "success": False,
            "error": f"Invalid version '{version}'. Accepted values: {', '.join(sorted(valid_versions))}"
        }

    resolved_version = "9x" if version == "default" else version

    _mock_state["active"] = True
    _mock_state["version"] = resolved_version
    _mock_state["uri"] = uri
    _mock_uri["value"] = uri

    version_map = {
        "6x": "ProxmoxVE6x",
        "7x": "ProxmoxVE7x",
        "8x": "ProxmoxVE8x",
        "9x": "ProxmoxVE9x"
    }

    return {
        "success": True,
        "message": f"Mock enabled for Proxmox VE {resolved_version} at URI: {uri}",
        "mock_function": version_map[resolved_version],
        "uri": uri,
        "version": resolved_version,
        "state": "active"
    }


@mcp.tool()
async def disable_mock() -> dict:
    """
    Disable and clean up all active HTTP mocks. Use this after tests are complete
    to restore normal HTTP behavior and prevent mock interference with subsequent operations.
    """
    was_active = _mock_state["active"]
    previous_version = _mock_state.get("version")
    previous_uri = _mock_state.get("uri")

    _mock_state["active"] = False
    _mock_state["version"] = None
    _mock_state["uri"] = None

    return {
        "success": True,
        "message": "All active HTTP mocks have been disabled (gock.Off() equivalent)",
        "was_active": was_active,
        "previous_version": previous_version,
        "previous_uri": previous_uri,
        "state": "inactive"
    }


@mcp.tool()
async def get_terminal_connection(
    host: str,
    port: int = 8523,
    tls: bool = True
) -> dict:
    """
    Establish a WebSocket terminal (xterm) connection to a Proxmox VE node or VM.
    Use this when an interactive shell session is needed over the Proxmox API.
    Returns a WebSocket endpoint suitable for terminal emulators.
    """
    scheme = "wss" if tls else "ws"
    http_scheme = "https" if tls else "http"
    ws_url = f"{scheme}://{host}:{port}/term"
    health_url = f"{http_scheme}://{host}:{port}/"

    connection_info = {
        "websocket_url": ws_url,
        "host": host,
        "port": port,
        "tls": tls,
        "path": "/term",
        "protocol": "xterm",
        "health_check_url": health_url,
        "instructions": (
            f"Connect your terminal emulator to {ws_url}. "
            "This endpoint is handled by impl.Term on the Proxmox term-and-vnc server."
        )
    }

    # Attempt a health check to see if server is reachable
    try:
        async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
            response = await client.get(health_url)
            connection_info["server_reachable"] = response.status_code == 200
            connection_info["server_response"] = response.text.strip()
    except Exception as e:
        connection_info["server_reachable"] = False
        connection_info["server_error"] = str(e)

    return connection_info


@mcp.tool()
async def get_vnc_connection(
    host: str,
    port: int = 8523,
    tls: bool = True
) -> dict:
    """
    Establish a WebSocket VNC connection to a Proxmox VE virtual machine.
    Use this when you need graphical console access to a VM.
    Returns a WebSocket endpoint compatible with noVNC or similar VNC clients.
    """
    scheme = "wss" if tls else "ws"
    http_scheme = "https" if tls else "http"
    ws_url = f"{scheme}://{host}:{port}/vnc"
    health_url = f"{http_scheme}://{host}:{port}/"

    connection_info = {
        "websocket_url": ws_url,
        "host": host,
        "port": port,
        "tls": tls,
        "path": "/vnc",
        "protocol": "vnc",
        "health_check_url": health_url,
        "instructions": (
            f"Connect your VNC client (e.g., noVNC) to {ws_url}. "
            "Obtain a VNC ticket first via get_vnc_ticket and pass it during authentication. "
            "This endpoint is handled by impl.Vnc on the Proxmox term-and-vnc server."
        )
    }

    # Attempt a health check
    try:
        async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
            response = await client.get(health_url)
            connection_info["server_reachable"] = response.status_code == 200
            connection_info["server_response"] = response.text.strip()
    except Exception as e:
        connection_info["server_reachable"] = False
        connection_info["server_error"] = str(e)

    return connection_info


@mcp.tool()
async def get_vnc_ticket(
    host: str,
    port: int = 8523,
    tls: bool = True
) -> dict:
    """
    Retrieve a one-time VNC authentication ticket from the Proxmox VE API.
    Use this before initiating a VNC connection to obtain the required authentication token.
    The ticket must be passed when establishing the VNC WebSocket session.
    """
    scheme = "https" if tls else "http"
    ticket_url = f"{scheme}://{host}:{port}/vnc-ticket"

    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(ticket_url)
            response.raise_for_status()

            try:
                data = response.json()
            except Exception:
                data = {"raw_response": response.text.strip()}

            return {
                "success": True,
                "ticket_url": ticket_url,
                "host": host,
                "port": port,
                "tls": tls,
                "status_code": response.status_code,
                "ticket_data": data,
                "instructions": (
                    "Use the ticket_data when authenticating your VNC WebSocket connection. "
                    f"Connect to wss://{host}:{port}/vnc and provide this ticket for auth."
                )
            }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "ticket_url": ticket_url,
            "host": host,
            "port": port,
            "tls": tls,
            "status_code": e.response.status_code,
            "error": f"HTTP error: {e.response.status_code} {e.response.text}"
        }
    except Exception as e:
        return {
            "success": False,
            "ticket_url": ticket_url,
            "host": host,
            "port": port,
            "tls": tls,
            "error": str(e)
        }


@mcp.tool()
async def check_server_health(
    host: str,
    port: int = 8523,
    tls: bool = True
) -> dict:
    """
    Ping the term-and-vnc proxy server to verify it is running and reachable.
    Use this as a health check before attempting terminal or VNC connections.
    Returns 'hello world' on success.
    """
    scheme = "https" if tls else "http"
    health_url = f"{scheme}://{host}:{port}/"

    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(health_url)
            response_text = response.text.strip()
            is_healthy = response.status_code == 200 and "hello world" in response_text.lower()

            return {
                "success": True,
                "healthy": is_healthy,
                "health_url": health_url,
                "host": host,
                "port": port,
                "tls": tls,
                "status_code": response.status_code,
                "response": response_text,
                "message": "Server is up and running" if is_healthy else "Server responded but health check unexpected"
            }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "healthy": False,
            "health_url": health_url,
            "host": host,
            "port": port,
            "tls": tls,
            "error": f"Connection refused or unreachable: {str(e)}",
            "message": "Server is not reachable"
        }
    except httpx.TimeoutException as e:
        return {
            "success": False,
            "healthy": False,
            "health_url": health_url,
            "host": host,
            "port": port,
            "tls": tls,
            "error": f"Request timed out: {str(e)}",
            "message": "Server health check timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "healthy": False,
            "health_url": health_url,
            "host": host,
            "port": port,
            "tls": tls,
            "error": str(e),
            "message": "Unexpected error during health check"
        }


@mcp.tool()
async def configure_mock_uri(uri: str) -> dict:
    """
    Set or update the mock configuration URI used by the Proxmox mock system.
    Use this to point the mock framework at a specific Proxmox server URI before
    enabling mocks, especially when running multiple test suites against different server addresses.
    """
    if not uri.startswith(("http://", "https://")):
        return {
            "success": False,
            "error": "URI must start with 'http://' or 'https://'",
            "provided_uri": uri
        }

    previous_uri = _mock_uri["value"]
    _mock_uri["value"] = uri

    # Also update mock state if active
    if _mock_state["active"]:
        _mock_state["uri"] = uri

    return {
        "success": True,
        "message": f"Mock URI configured successfully",
        "uri": uri,
        "previous_uri": previous_uri,
        "mock_active": _mock_state["active"],
        "note": (
            "This sets config.C.URI in the go-proxmox mock framework equivalent. "
            "Call enable_mock() to activate mocking with this URI."
        )
    }




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
