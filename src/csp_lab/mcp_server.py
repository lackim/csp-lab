"""Read-only MCP tools for inspecting a running CSP lab."""

import json
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from csp_lab import docker

server = MCPServer("csp-lab")
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@server.tool(
    name="csp_topology",
    description="Read the configured CSP protocol version and simulated nodes",
    annotations=READ_ONLY,
)
def csp_topology() -> str:
    return json.dumps(docker.topology().as_json())


@server.tool(
    name="csp_ping",
    description="Ping a simulated node through libcsp",
    annotations=READ_ONLY,
)
def csp_ping(address: Annotated[int, Field(strict=True, gt=0)]) -> str:
    state = docker.require_state()
    target = docker.parse_address(str(address), state.protocol)
    return json.dumps(docker.ping(target).as_json())


@server.tool(
    name="csp_diagnose",
    description="Ping both nodes and summarize lab health",
    annotations=READ_ONLY,
)
def csp_diagnose() -> str:
    return json.dumps(docker.diagnose().as_json())
