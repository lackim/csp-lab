import assert from "node:assert/strict";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const client = new Client({ name: "csp-lab-smoke", version: "0.1.0" });
const transport = new StdioClientTransport({ command: process.execPath, args: ["dist/mcp.js"], cwd: process.cwd() });

try {
  await client.connect(transport);
  const tools = await client.listTools();
  assert.deepEqual(tools.tools.map((tool) => tool.name).sort(), ["csp_diagnose", "csp_ping", "csp_topology"]);

  async function call(name, args = {}) {
    const response = await client.callTool({ name, arguments: args });
    assert.equal(response.isError, undefined, JSON.stringify(response));
    assert.equal(response.content[0]?.type, "text");
    return JSON.parse(response.content[0].text);
  }

  const topology = await call("csp_topology");
  assert.ok(topology.protocol === 1 || topology.protocol === 2);
  assert.deepEqual(topology.nodes, [2, 3]);
  assert.equal((await call("csp_ping", { address: 2 })).reachable, true);
  assert.equal((await call("csp_ping", { address: 5 })).reachable, false);
  assert.equal((await call("csp_diagnose")).healthy, true);
  console.log(`MCP smoke test passed for CSP v${topology.protocol}`);
} finally {
  await client.close();
}
