import assert from "node:assert/strict";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const client = new Client({ name: "csp-lab-smoke", version: "0.1.0" });
const transport = new StdioClientTransport({ command: process.execPath, args: ["dist/mcp.js"], cwd: process.cwd() });

try {
  await client.connect(transport);
  const tools = await client.listTools();
  assert.deepEqual(tools.tools.map((tool) => tool.name).sort(), ["csp_diagnose", "csp_ping", "csp_topology"]);

  async function call<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
    const response = await client.callTool({ name, arguments: args });
    assert.equal(response.isError, undefined, JSON.stringify(response));
    const content = Array.isArray(response.content) ? response.content[0] : undefined;
    if (content?.type !== "text" || typeof content.text !== "string") {
      throw new Error(`No text response from ${name}`);
    }
    return JSON.parse(content.text) as T;
  }

  const topology = await call<{ protocol: 1 | 2; nodes: number[] }>("csp_topology");
  assert.ok(topology.protocol === 1 || topology.protocol === 2);
  assert.deepEqual(topology.nodes, [2, 3]);
  assert.equal((await call<{ reachable: boolean }>("csp_ping", { address: 2 })).reachable, true);
  assert.equal((await call<{ reachable: boolean }>("csp_ping", { address: 5 })).reachable, false);
  assert.equal((await call<{ healthy: boolean }>("csp_diagnose")).healthy, true);
  console.log(`MCP smoke test passed for CSP v${topology.protocol}`);
} finally {
  await client.close();
}
