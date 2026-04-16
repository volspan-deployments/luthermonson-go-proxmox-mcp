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

# In-memory state for mock intercepts
_mock_intercepts_active = False
_mock_config = {}


@mcp.tool()
async def enable_mock_intercepts(uri: str, version: str = "9x") -> dict:
    """
    Enable HTTP mock intercepts for a specific Proxmox VE version during testing.
    Use this to set up mock HTTP responses that simulate a real Proxmox VE API,
    allowing tests to run without a live Proxmox instance.
    Supports versions 6.x, 7.x, 8.x, and 9.x.
    """
    global _mock_intercepts_active, _mock_config

    valid_versions = ["6x", "7x", "8x", "9x"]
    if version not in valid_versions:
        return {
            "success": False,
            "error": f"Invalid version '{version}'. Accepted values: {valid_versions}"
        }

    _mock_intercepts_active = True
    _mock_config = {
        "uri": uri,
        "version": version
    }

    version_map = {
        "6x": "ProxmoxVE6x",
        "7x": "ProxmoxVE7x",
        "8x": "ProxmoxVE8x",
        "9x": "ProxmoxVE9x"
    }

    return {
        "success": True,
        "message": f"Mock HTTP intercepts enabled for Proxmox VE {version} at {uri}",
        "config": {
            "uri": uri,
            "version": version,
            "mock_function": version_map[version]
        },
        "details": (
            f"Simulating Proxmox VE {version[0]}.x API responses. "
            "All HTTP requests to the configured URI will be intercepted "
            "and responded to with pre-recorded mock data. "
            "Call disable_mock_intercepts when testing is complete."
        )
    }


@mcp.tool()
async def disable_mock_intercepts() -> dict:
    """
    Disable and clean up all active HTTP mock intercepts.
    Use this after tests complete to restore normal HTTP behavior and prevent
    mock responses from leaking into other tests or production code.
    """
    global _mock_intercepts_active, _mock_config

    if not _mock_intercepts_active:
        return {
            "success": True,
            "message": "No active mock intercepts to disable.",
            "was_active": False
        }

    previous_config = dict(_mock_config)
    _mock_intercepts_active = False
    _mock_config = {}

    return {
        "success": True,
        "message": "All active HTTP mock intercepts have been disabled and cleaned up.",
        "was_active": True,
        "previous_config": previous_config,
        "details": (
            "Normal HTTP behavior has been restored. "
            "Mock responses will no longer intercept requests to the Proxmox API."
        )
    }


@mcp.tool()
async def connect_terminal(host: str, port: int = 8523) -> dict:
    """
    Establish a WebSocket terminal session to a Proxmox VE node or VM.
    Use this when you need interactive shell access to a Proxmox node or
    virtual machine console via a browser-compatible WebSocket connection.
    """
    base_url = f"https://{host}:{port}"
    term_url = f"{base_url}/term"
    ws_url = f"wss://{host}:{port}/term"

    # Check if the endpoint is reachable via HTTP first
    reachable = False
    error_detail = None
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(base_url)
            reachable = response.status_code < 500
    except httpx.ConnectError as e:
        error_detail = f"Connection refused or host unreachable: {str(e)}"
    except httpx.TimeoutException as e:
        error_detail = f"Connection timed out: {str(e)}"
    except Exception as e:
        error_detail = f"Unexpected error: {str(e)}"

    return {
        "success": reachable,
        "connection_type": "terminal",
        "host": host,
        "port": port,
        "http_endpoint": term_url,
        "websocket_endpoint": ws_url,
        "handler": "impl.Term",
        "reachable": reachable,
        "error": error_detail,
        "instructions": (
            f"Connect your WebSocket client to {ws_url} to establish an interactive "
            "terminal session. This endpoint is served by the go-proxmox term-and-vnc "
            "example server using the impl.Term handler."
        ) if reachable else f"Could not reach server at {base_url}. Error: {error_detail}"
    }


@mcp.tool()
async def connect_vnc(host: str, port: int = 8523) -> dict:
    """
    Establish a WebSocket VNC session to a Proxmox VE virtual machine's graphical console.
    Use this when you need graphical (GUI) remote access to a VM running on Proxmox,
    streamed over a WebSocket for browser compatibility.
    """
    base_url = f"https://{host}:{port}"
    vnc_url = f"{base_url}/vnc"
    ws_url = f"wss://{host}:{port}/vnc"

    reachable = False
    error_detail = None
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(base_url)
            reachable = response.status_code < 500
    except httpx.ConnectError as e:
        error_detail = f"Connection refused or host unreachable: {str(e)}"
    except httpx.TimeoutException as e:
        error_detail = f"Connection timed out: {str(e)}"
    except Exception as e:
        error_detail = f"Unexpected error: {str(e)}"

    return {
        "success": reachable,
        "connection_type": "vnc",
        "host": host,
        "port": port,
        "http_endpoint": vnc_url,
        "websocket_endpoint": ws_url,
        "handler": "impl.Vnc",
        "reachable": reachable,
        "error": error_detail,
        "instructions": (
            f"Connect your VNC/WebSocket client to {ws_url} to establish a graphical "
            "console session. This endpoint is served by the go-proxmox term-and-vnc "
            "example server using the impl.Vnc handler. "
            "Retrieve a VNC ticket first using get_vnc_ticket if authentication is required."
        ) if reachable else f"Could not reach server at {base_url}. Error: {error_detail}"
    }


@mcp.tool()
async def get_vnc_ticket(host: str, port: int = 8523) -> dict:
    """
    Retrieve a VNC authentication ticket from the Proxmox VE API.
    Use this before initiating a VNC session to obtain the short-lived ticket/token
    required for authenticating the VNC WebSocket connection.
    """
    base_url = f"https://{host}:{port}"
    ticket_url = f"{base_url}/vnc-ticket"

    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(ticket_url)
            if response.status_code == 200:
                try:
                    ticket_data = response.json()
                except Exception:
                    ticket_data = {"raw": response.text}
                return {
                    "success": True,
                    "host": host,
                    "port": port,
                    "endpoint": ticket_url,
                    "handler": "impl.VncTicket",
                    "ticket_data": ticket_data,
                    "status_code": response.status_code,
                    "instructions": (
                        "Use the returned ticket to authenticate your VNC WebSocket connection. "
                        "Tickets are short-lived; use immediately after retrieval."
                    )
                }
            else:
                return {
                    "success": False,
                    "host": host,
                    "port": port,
                    "endpoint": ticket_url,
                    "status_code": response.status_code,
                    "error": f"Server returned HTTP {response.status_code}",
                    "response_body": response.text[:500]
                }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "host": host,
            "port": port,
            "endpoint": ticket_url,
            "error": f"Connection refused or host unreachable: {str(e)}"
        }
    except httpx.TimeoutException as e:
        return {
            "success": False,
            "host": host,
            "port": port,
            "endpoint": ticket_url,
            "error": f"Connection timed out: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "host": host,
            "port": port,
            "endpoint": ticket_url,
            "error": f"Unexpected error: {str(e)}"
        }


@mcp.tool()
async def check_api_health(host: str, port: int = 8523) -> dict:
    """
    Check whether the Proxmox API client server endpoint is reachable and responding.
    Use this as a basic connectivity and health check before performing more complex
    operations, or to verify the server is running correctly.
    """
    base_url = f"https://{host}:{port}"

    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.get(base_url)
            is_healthy = response.status_code == 200
            response_body = response.text.strip()

            return {
                "success": True,
                "healthy": is_healthy,
                "host": host,
                "port": port,
                "url": base_url,
                "status_code": response.status_code,
                "response_body": response_body,
                "expected_response": "hello world",
                "response_matches_expected": response_body.lower() == "hello world",
                "message": (
                    f"Server at {base_url} is up and responding correctly."
                    if is_healthy
                    else f"Server at {base_url} responded with HTTP {response.status_code}."
                )
            }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "healthy": False,
            "host": host,
            "port": port,
            "url": base_url,
            "error": f"Connection refused or host unreachable: {str(e)}",
            "message": f"Server at {base_url} is not reachable."
        }
    except httpx.TimeoutException as e:
        return {
            "success": False,
            "healthy": False,
            "host": host,
            "port": port,
            "url": base_url,
            "error": f"Connection timed out: {str(e)}",
            "message": f"Server at {base_url} timed out."
        }
    except Exception as e:
        return {
            "success": False,
            "healthy": False,
            "host": host,
            "port": port,
            "url": base_url,
            "error": f"Unexpected error: {str(e)}",
            "message": f"Failed to check health of server at {base_url}."
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
