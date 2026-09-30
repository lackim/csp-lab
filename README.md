# csp-lab

A local CubeSat Space Protocol lab powered by [libcsp](https://github.com/libcsp/libcsp). It runs two simulated nodes and a ZMQ hub in Docker. The Python CLI and MCP server provide the same diagnostic operations.

## Requirements

- Python 3.10 or newer and [uv](https://docs.astral.sh/uv/)
- Docker with Compose
- Git submodules

The native code is built against the pinned `libcsp` v2.1 submodule. That library supports CSP protocol v1 and v2 at runtime. The Docker image uses Linux because recent libcsp releases do not provide maintained native macOS support. See [versioning policy](docs/versioning.md) for the supported combinations.

## Start

```sh
git submodule update --init --recursive
uv sync --locked
uv run --locked csp-lab up --protocol 2
uv run --locked csp-lab ping 2 --json
uv run --locked csp-lab doctor --json
uv run --locked csp-lab down
```

To test CSP v1, start a fresh lab with `up --protocol 1`. `up` refuses to change the protocol of an existing lab; run `down` first. The two versions never share a running network.

The CLI commands are `up`, `down`, `status`, `topology`, `ping <address>`, and `doctor`. `status` checks whether the hub and both nodes are running in Docker. Use `--json` for machine-readable output. `ping` and `doctor` return a nonzero exit code when a node does not respond. Addresses must be 1–31 for CSP v1 or 1–16383 for CSP v2. The demo nodes use addresses 2 and 3; the probe uses address 4.

## MCP

Start the lab first, then configure an MCP client to launch `csp-lab mcp` over stdio. For clients that use an `mcpServers` JSON configuration, the entry for a local clone is:

```json
{
  "mcpServers": {
    "csp-lab": {
      "command": "/absolute/path/to/csp-lab/.venv/bin/csp-lab",
      "args": ["mcp"]
    }
  }
}
```

Replace the path with the absolute path to your clone after `uv sync`. If your client uses a different configuration format, set its command to the installed `csp-lab` executable, its argument to `mcp`, and its transport to stdio. Keep the lab running in a separate terminal while using the MCP tools.

The stdio server provides `csp_topology`, `csp_ping`, and `csp_diagnose`. It does not expose commands that reboot, shut down, or read or write node memory. The simulated server binds only the CSP ping service port.

## What is tested

`uv run --locked pytest` checks protocol validation, unavailable Docker, cleanup after a failed start, and unresponsive nodes without requiring Docker. Run `uv run --locked ruff check src/csp_lab tests` and `uv run --locked mypy src/csp_lab` for lint and types. The Docker integration flow exercises real libcsp, ZMQ, CLI diagnostics, and MCP for both protocol versions:

```sh
bash test/integration.sh
```

The GitHub Actions workflow is configured to run both checks on Ubuntu with Docker Compose. The local integration script stops the lab on exit, including after a failure.

## Scope and license

This lab uses the pinned libcsp release as a build dependency and selects CSP v1 or v2 as a runtime protocol. It does not build against historical libcsp 1.x releases or operate mixed-version networks. ZMQ is the only transport in this lab. The project is licensed under [MIT](LICENSE); the libcsp submodule retains its own license.
