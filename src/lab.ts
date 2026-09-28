import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, rm, mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export type Protocol = 1 | 2;
export type PingResult = { target: number; reachable: boolean; rttMs: number };
export type LabState = { protocol: Protocol; libcspVersion: "2.1" };

export const root = dirname(dirname(fileURLToPath(import.meta.url)));
const stateDir = join(root, ".csp-lab");
const stateFile = join(stateDir, "state.json");
const project = `csp-lab-${createHash("sha256").update(root).digest("hex").slice(0, 8)}`;

export function parseProtocol(input: string): Protocol {
  if (input !== "1" && input !== "2") throw new Error("Protocol must be 1 or 2.");
  return Number(input) as Protocol;
}

export function parseAddress(input: string, protocol: Protocol): number {
  if (!/^[0-9]+$/.test(input)) throw new Error("Address must be a decimal integer.");
  const value = Number(input);
  const max = protocol === 1 ? 31 : 16383;
  if (!Number.isSafeInteger(value) || value < 1 || value > max) {
    throw new Error(`CSP v${protocol} address must be between 1 and ${max}.`);
  }
  return value;
}

export async function getState(): Promise<LabState | null> {
  try {
    const state = JSON.parse(await readFile(stateFile, "utf8")) as LabState;
    if ((state.protocol !== 1 && state.protocol !== 2) || state.libcspVersion !== "2.1") {
      throw new Error("Unsupported lab state. Run 'csp-lab down' and start again.");
    }
    return state;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}

export async function requireState(): Promise<LabState> {
  const state = await getState();
  if (!state) throw new Error("Lab is not started. Run 'csp-lab up --protocol 2' first.");
  return state;
}

export async function run(command: string, args: string[], env: NodeJS.ProcessEnv = {}, timeoutMs = 120_000): Promise<string> {
  return await new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: root, env: { ...process.env, ...env }, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => child.kill("SIGTERM"), timeoutMs);
    child.stdout.setEncoding("utf8").on("data", (chunk: string) => { stdout += chunk; });
    child.stderr.setEncoding("utf8").on("data", (chunk: string) => { stderr += chunk; });
    child.on("error", (error) => { clearTimeout(timer); reject(error); });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(stdout);
      else reject(new Error(`${command} exited ${code ?? "without a code"}: ${stderr.trim() || stdout.trim()}`));
    });
  });
}

async function compose(args: string[], protocol: Protocol, timeoutMs?: number): Promise<string> {
  return run("docker", ["compose", "-p", project, "-f", "compose.yaml", ...args], { CSP_VERSION: String(protocol) }, timeoutMs);
}

export async function up(protocol: Protocol): Promise<LabState> {
  if (await getState()) throw new Error("Lab is already started. Run 'csp-lab down' before changing protocol.");
  try {
    await compose(["up", "-d", "--build", "hub", "node2", "node3"], protocol, 600_000);
    for (const target of [2, 3]) {
      const result = await pingWithProtocol(target, protocol);
      if (!result.reachable) throw new Error(`Node ${target} did not answer a CSP v${protocol} ping.`);
    }
    const state: LabState = { protocol, libcspVersion: "2.1" };
    await mkdir(stateDir, { recursive: true });
    await writeFile(stateFile, `${JSON.stringify(state, null, 2)}\n`);
    return state;
  } catch (error) {
    await compose(["down", "--remove-orphans"], protocol).catch(() => {});
    await rm(stateDir, { recursive: true, force: true });
    throw error;
  }
}

export async function down(): Promise<void> {
  const state = await getState();
  await compose(["down", "--remove-orphans"], state?.protocol ?? 2);
  await rm(stateDir, { recursive: true, force: true });
}

export async function pingWithProtocol(target: number, protocol: Protocol): Promise<PingResult> {
  parseAddress(String(target), protocol);
  const output = await compose(["run", "--rm", "--no-deps", "probe", "probe", String(protocol), "4", String(target)], protocol, 30_000);
  const line = output.trim().split("\n").reverse().find((candidate) => candidate.startsWith('{"target":'));
  if (!line) throw new Error(`Probe returned no JSON result: ${output.trim()}`);
  const result = JSON.parse(line) as PingResult;
  if (result.target !== target || typeof result.reachable !== "boolean" || !Number.isInteger(result.rttMs)) {
    throw new Error("Probe returned an invalid result.");
  }
  return result;
}

export async function ping(target: number): Promise<PingResult> {
  const state = await requireState();
  return pingWithProtocol(target, state.protocol);
}

export async function topology(): Promise<object> {
  const state = await requireState();
  return { protocol: state.protocol, libcspVersion: state.libcspVersion, nodes: [2, 3], probeAddress: 4, transport: "ZMQ" };
}

export async function diagnose(): Promise<object> {
  const state = await requireState();
  const nodes = [];
  for (const target of [2, 3]) nodes.push(await pingWithProtocol(target, state.protocol));
  return { protocol: state.protocol, libcspVersion: state.libcspVersion, healthy: nodes.every((node) => node.reachable), nodes };
}

export async function status(): Promise<{ running: boolean; state: LabState | null }> {
  const state = await getState();
  if (!state) return { running: false, state: null };
  const services = (await compose(["ps", "--services", "--status", "running"], state.protocol))
    .trim().split("\n").filter(Boolean);
  return { running: ["hub", "node2", "node3"].every((service) => services.includes(service)), state };
}
