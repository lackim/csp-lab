import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { copyFile, mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function makeLab() {
  const root = await mkdtemp(join(tmpdir(), "csp-lab-failure-"));
  await mkdir(join(root, "dist"));
  await mkdir(join(root, "bin"));
  for (const name of ["cli.js", "lab.js"]) {
    await copyFile(join(repo, "dist", name), join(root, "dist", name));
  }
  await writeFile(join(root, "package.json"), '{"type":"module"}\n');
  await symlink(join(repo, "node_modules"), join(root, "node_modules"), "dir");
  return root;
}

async function runCli(root: string, args: string[], env: NodeJS.ProcessEnv = {}) {
  return await new Promise<{ code: number | null; stdout: string; stderr: string }>((resolveResult, reject) => {
    const child = spawn(process.execPath, [join(root, "dist/cli.js"), ...args], {
      cwd: root,
      env: { ...process.env, SHIPCLI_DISABLE_UPDATE_CHECK: "1", ...env },
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => child.kill("SIGKILL"), 15_000);
    child.stdout.setEncoding("utf8").on("data", (part) => { stdout += part; });
    child.stderr.setEncoding("utf8").on("data", (part) => { stderr += part; });
    child.on("error", reject);
    child.on("close", (code) => {
      clearTimeout(timer);
      resolveResult({ code, stdout, stderr });
    });
  });
}

async function putState(root: string, protocol = 2) {
  await mkdir(join(root, ".csp-lab"));
  await writeFile(join(root, ".csp-lab/state.json"), JSON.stringify({ protocol, libcspVersion: "2.1" }));
}

test("status reports an error when Docker is unavailable", async () => {
  const root = await makeLab();
  try {
    await putState(root);
    const result = await runCli(root, ["status", "--json"], { PATH: join(root, "bin") });
    assert.equal(result.code, 1, result.stderr);
    assert.match(JSON.parse(result.stdout).error, /ENOENT/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("failed Docker startup removes partially started services", async () => {
  const root = await makeLab();
  try {
    const log = join(root, "docker.log");
    const docker = join(root, "bin/docker");
    await writeFile(docker, `#!/bin/sh
printf '%s\n' "$*" >> "$CSP_LAB_FAKE_LOG"
for arg do
  if [ "$arg" = up ]; then exit 17; fi
done
exit 0
`, { mode: 0o755 });
    const result = await runCli(root, ["up", "--protocol", "2", "--json"], {
      PATH: `${join(root, "bin")}:${process.env.PATH}`,
      CSP_LAB_FAKE_LOG: log
    });
    assert.equal(result.code, 1, result.stderr);
    assert.match(JSON.parse(result.stdout).error, /docker exited 17/);
    const calls = await readFile(log, "utf8");
    assert.match(calls, /\bup -d --build hub node2 node3\b/);
    assert.match(calls, /\bdown --remove-orphans\b/);
    await assert.rejects(readFile(join(root, ".csp-lab/state.json")), { code: "ENOENT" });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("ping and doctor signal an unresponsive node", async () => {
  const root = await makeLab();
  try {
    await putState(root);
    await writeFile(join(root, "bin/docker"), `#!/bin/sh
for arg do target=$arg; done
case "$target" in
  2) printf '%s\n' '{"target":2,"reachable":true,"rttMs":3}' ;;
  3) printf '%s\n' '{"target":3,"reachable":false,"rttMs":-1}' ;;
esac
`, { mode: 0o755 });
    const env = { PATH: `${join(root, "bin")}:${process.env.PATH}` };
    const ping = await runCli(root, ["ping", "3", "--json"], env);
    assert.equal(ping.code, 1, ping.stderr);
    assert.deepEqual(JSON.parse(ping.stdout), { target: 3, reachable: false, rttMs: -1 });
    const doctor = await runCli(root, ["doctor", "--json"], env);
    assert.equal(doctor.code, 1, doctor.stderr);
    const report = JSON.parse(doctor.stdout) as {
      healthy: boolean;
      nodes: Array<{ target: number; reachable: boolean }>;
    };
    assert.equal(report.healthy, false);
    assert.deepEqual(report.nodes.map(({ target, reachable }) => [target, reachable]), [[2, true], [3, false]]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
