#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod/v4";
import { diagnose, parseAddress, ping, requireState, topology } from "./lab.js";

const server = new McpServer({ name: "csp-lab", version: "0.1.0" });

function result(value: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(value) }] };
}

function failure(error: unknown) {
  return { content: [{ type: "text" as const, text: error instanceof Error ? error.message : String(error) }], isError: true };
}

server.registerTool("csp_topology", {
  description: "Read the configured CSP protocol version and simulated nodes",
  annotations: { readOnlyHint: true, openWorldHint: false }
}, async () => {
  try { return result(await topology()); } catch (error) { return failure(error); }
});

server.registerTool("csp_ping", {
  description: "Ping a simulated node through libcsp",
  inputSchema: { address: z.number().int().positive() },
  annotations: { readOnlyHint: true, openWorldHint: false }
}, async ({ address }) => {
  try {
    const state = await requireState();
    return result(await ping(parseAddress(String(address), state.protocol)));
  } catch (error) { return failure(error); }
});

server.registerTool("csp_diagnose", {
  description: "Ping both nodes and summarize lab health",
  annotations: { readOnlyHint: true, openWorldHint: false }
}, async () => {
  try { return result(await diagnose()); } catch (error) { return failure(error); }
});

await server.connect(new StdioServerTransport());
