"""Exercise the Python MCP server against the running native lab."""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "csp_lab.cli", "mcp"],
        env=os.environ.copy(),
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert sorted(tool.name for tool in tools.tools) == [
                "csp_diagnose",
                "csp_ping",
                "csp_topology",
            ]
            assert all(tool.annotations and tool.annotations.readOnlyHint for tool in tools.tools)

            async def call(name: str, arguments: dict[str, int] | None = None) -> dict[str, object]:
                result = await session.call_tool(name, arguments or {})
                assert not result.isError, result
                assert result.content and result.content[0].type == "text"
                return json.loads(result.content[0].text)

            topology = await call("csp_topology")
            assert topology["protocol"] in (1, 2)
            assert topology["nodes"] == [2, 3]
            assert (await call("csp_ping", {"address": 2}))["reachable"] is True
            assert (await call("csp_ping", {"address": 5}))["reachable"] is False
            assert (await call("csp_diagnose"))["healthy"] is True
            invalid = await session.call_tool("csp_ping", {"address": True})
            assert invalid.isError
            maximum = 31 if topology["protocol"] == 1 else 16383
            invalid = await session.call_tool("csp_ping", {"address": maximum + 1})
            assert invalid.isError
            print(f"MCP smoke test passed for CSP v{topology['protocol']}")


if __name__ == "__main__":
    asyncio.run(main())
