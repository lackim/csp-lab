#!/usr/bin/env node
import { createCLI } from "@shipcli/core";
import { diagnose, down, parseAddress, parseProtocol, ping, requireState, status, topology, up } from "./lab.js";

const cli = createCLI({ name: "csp-lab", packageName: "csp-lab", description: "Local CubeSat Space Protocol lab", version: "0.1.0" });

function output(value: unknown): void {
  if (cli.opts().json) console.log(JSON.stringify(value));
  else console.log(typeof value === "string" ? value : JSON.stringify(value, null, 2));
}

function action<T extends unknown[]>(fn: (...args: T) => Promise<unknown>) {
  return async (...args: T) => {
    try { output(await fn(...args)); }
    catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (cli.opts().json) console.log(JSON.stringify({ error: message }));
      else console.error(`csp-lab: ${message}`);
      process.exitCode = 1;
    }
  };
}

cli.command("up").description("Start two libcsp nodes and a ZMQ hub")
  .option("--protocol <version>", "CSP protocol version (1 or 2)", "2")
  .action(action(async (options: { protocol: string }) => ({ started: true, ...await up(parseProtocol(options.protocol)) })));

cli.command("down").description("Stop the lab")
  .action(action(async () => { await down(); return { stopped: true }; }));

cli.command("ping <address>").description("Ping a node using libcsp")
  .action(action(async (address: string) => {
    const state = await requireState();
    const result = await ping(parseAddress(address, state.protocol));
    if (!result.reachable) process.exitCode = 1;
    return result;
  }));

cli.command("topology").description("Show the configured network")
  .action(action(topology));

cli.command("doctor").description("Test both nodes")
  .action(action(async () => {
    const result = await diagnose() as { healthy: boolean };
    if (!result.healthy) process.exitCode = 1;
    return result;
  }));

cli.command("status").description("Show local lab state")
  .action(action(status));

await cli.run();
