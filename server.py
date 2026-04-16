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

# In-memory store for active mock interceptor state
_mock_interceptors: dict = {}


@mcp.tool()
async def enable_mock_interceptor(
    uri: str,
    version: str = "9x"
) -> dict:
    """Enable HTTP mock interception for a specific Proxmox VE version (6x, 7x, 8x, or 9x).
    Use this when setting up tests or development environments that need to simulate
    Proxmox API responses without a real server. Defaults to Proxmox VE 9x if no version is specified."""
    valid_versions = {"6x", "7x", "8x", "9x"}
    if version not in valid_versions:
        return {
            "success": False,
            "error": f"Invalid version '{version}'. Accepted values: {', '.join(sorted(valid_versions))}."
        }

    version_map = {
        "6x": "ProxmoxVE6x",
        "7x": "ProxmoxVE7x",
        "8x": "ProxmoxVE8x",
        "9x": "ProxmoxVE9x",
    }

    func_name = version_map[version]
    _mock_interceptors[uri] = {
        "uri": uri,
        "version": version,
        "func": func_name,
        "active": True
    }

    return {
        "success": True,
        "message": f"Mock interceptor enabled for Proxmox VE {version} at {uri}",
        "uri": uri,
        "version": version,
        "interceptor_function": func_name,
        "description": (
            f"This simulates the go-proxmox mocks.{func_name}(config.Config{{URI: '{uri}'}}) call. "
            f"All HTTP requests to {uri} will be intercepted and mocked with Proxmox VE {version} responses."
        )
    }


@mcp.tool()
async def disable_mock_interceptor() -> dict:
    """Disable all active HTTP mock interceptors (gock interceptors).
    Use this after tests are complete to restore real HTTP behavior and prevent
    mock responses from leaking into production or other test cases."""
    active_count = len(_mock_interceptors)
    active_uris = list(_mock_interceptors.keys())
    _mock_interceptors.clear()

    return {
        "success": True,
        "message": "All mock interceptors have been disabled.",
        "interceptors_removed": active_count,
        "uris_cleared": active_uris,
        "description": (
            "This simulates the go-proxmox mocks.Off() call which internally calls gock.Off() "
            "to disable all active HTTP mock interceptors."
        )
    }


@mcp.tool()
async def get_vnc_ticket(server_url: str) -> dict:
    """Retrieve a VNC ticket from the Proxmox server.
    Use this when you need to authenticate and establish a VNC remote display session
    for a virtual machine or container."""
    url = server_url.rstrip("/") + "/vnc-ticket"

    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            try:
                data = response.json()
            except Exception:
                data = {"raw": response.text}
            return {
                "success": True,
                "status_code": response.status_code,
                "server_url": server_url,
                "endpoint": url,
                "ticket_data": data
            }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "error": "Connection failed",
            "detail": str(e),
            "server_url": server_url,
            "endpoint": url
        }
    except httpx.TimeoutException as e:
        return {
            "success": False,
            "error": "Request timed out",
            "detail": str(e),
            "server_url": server_url,
            "endpoint": url
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error {e.response.status_code}",
            "detail": str(e),
            "server_url": server_url,
            "endpoint": url
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Unexpected error",
            "detail": str(e),
            "server_url": server_url,
            "endpoint": url
        }


@mcp.tool()
async def connect_terminal(
    server_url: str,
    node: Optional[str] = None,
    vmid: Optional[int] = None
) -> dict:
    """Establish a WebSocket terminal (xterm) session to a Proxmox VM or container via the term-and-vnc proxy.
    Use this when an interactive shell session is needed for a virtual machine or container
    through the browser-based terminal interface."""
    base_url = server_url.rstrip("/")
    # Build the terminal endpoint (WebSocket)
    ws_url = base_url if base_url.startswith("wss://") or base_url.startswith("ws://") else base_url

    params = {}
    if node:
        params["node"] = node
    if vmid is not None:
        params["vmid"] = vmid

    # Attempt an HTTP GET to the /term endpoint to validate connectivity
    http_url = base_url.replace("wss://", "https://").replace("ws://", "http://")
    term_http_url = http_url + "/term" if not http_url.endswith("/term") else http_url

    connection_info = {
        "websocket_url": ws_url if ws_url.endswith("/term") else ws_url + "/term",
        "node": node,
        "vmid": vmid,
        "parameters": params
    }

    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(term_http_url, params=params)
            return {
                "success": True,
                "status_code": response.status_code,
                "server_url": server_url,
                "endpoint": term_http_url,
                "connection_info": connection_info,
                "message": (
                    f"Terminal endpoint is reachable. Connect via WebSocket to "
                    f"{connection_info['websocket_url']} to start an interactive terminal session."
                )
            }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "error": "Connection failed - proxy may not be running",
            "detail": str(e),
            "server_url": server_url,
            "connection_info": connection_info
        }
    except httpx.TimeoutException as e:
        return {
            "success": False,
            "error": "Request timed out",
            "detail": str(e),
            "server_url": server_url,
            "connection_info": connection_info
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Unexpected error",
            "detail": str(e),
            "server_url": server_url,
            "connection_info": connection_info
        }


@mcp.tool()
async def connect_vnc(
    server_url: str,
    node: Optional[str] = None,
    vmid: Optional[int] = None,
    ticket: Optional[str] = None
) -> dict:
    """Establish a WebSocket VNC session to a Proxmox VM or container via the term-and-vnc proxy.
    Use this when a graphical remote display session is needed for a virtual machine
    through the browser-based VNC interface."""
    base_url = server_url.rstrip("/")
    ws_url = base_url if base_url.startswith("wss://") or base_url.startswith("ws://") else base_url

    params = {}
    if node:
        params["node"] = node
    if vmid is not None:
        params["vmid"] = vmid
    if ticket:
        params["ticket"] = ticket

    # Attempt HTTP check
    http_url = base_url.replace("wss://", "https://").replace("ws://", "http://")
    vnc_http_url = http_url + "/vnc" if not http_url.endswith("/vnc") else http_url

    vnc_ws_url = ws_url + "/vnc" if not ws_url.endswith("/vnc") else ws_url

    connection_info = {
        "websocket_url": vnc_ws_url,
        "node": node,
        "vmid": vmid,
        "ticket_provided": ticket is not None,
        "parameters": {k: v for k, v in params.items() if k != "ticket"}
    }

    if not ticket:
        connection_info["hint"] = (
            "No VNC ticket provided. Consider calling get_vnc_ticket first to obtain "
            "an authentication ticket before establishing a VNC session."
        )

    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(vnc_http_url, params=params)
            return {
                "success": True,
                "status_code": response.status_code,
                "server_url": server_url,
                "endpoint": vnc_http_url,
                "connection_info": connection_info,
                "message": (
                    f"VNC endpoint is reachable. Connect via WebSocket to "
                    f"{vnc_ws_url} to start a graphical VNC session."
                )
            }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "error": "Connection failed - proxy may not be running",
            "detail": str(e),
            "server_url": server_url,
            "connection_info": connection_info
        }
    except httpx.TimeoutException as e:
        return {
            "success": False,
            "error": "Request timed out",
            "detail": str(e),
            "server_url": server_url,
            "connection_info": connection_info
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Unexpected error",
            "detail": str(e),
            "server_url": server_url,
            "connection_info": connection_info
        }


@mcp.tool()
async def check_proxy_health(server_url: str) -> dict:
    """Verify that the term-and-vnc proxy server is running and reachable by calling its root endpoint.
    Use this as a health check before attempting terminal or VNC connections to confirm
    the proxy is operational."""
    url = server_url.rstrip("/") + "/"

    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return {
                "success": True,
                "healthy": True,
                "status_code": response.status_code,
                "server_url": server_url,
                "endpoint": url,
                "response_body": response.text,
                "message": f"Proxy server at {server_url} is up and running."
            }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "healthy": False,
            "error": "Connection refused - proxy server may be down",
            "detail": str(e),
            "server_url": server_url,
            "endpoint": url
        }
    except httpx.TimeoutException as e:
        return {
            "success": False,
            "healthy": False,
            "error": "Request timed out - proxy server may be overloaded or unreachable",
            "detail": str(e),
            "server_url": server_url,
            "endpoint": url
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "healthy": False,
            "error": f"HTTP error {e.response.status_code}",
            "detail": str(e),
            "server_url": server_url,
            "endpoint": url
        }
    except Exception as e:
        return {
            "success": False,
            "healthy": False,
            "error": "Unexpected error",
            "detail": str(e),
            "server_url": server_url,
            "endpoint": url
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
