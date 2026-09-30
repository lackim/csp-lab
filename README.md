# csp-lab

A local CubeSat Space Protocol lab powered by [libcsp](https://github.com/libcsp/libcsp). It runs two simulated nodes and a ZMQ hub in Docker. The Python CLI and MCP server provide the same diagnostic operations.

## Requirements

- Python 3.10 or newer and [uv](https://docs.astral.sh/uv/)
- Docker with Compose

The native code is built against the pinned `libcsp` v2.1 submodule. That library supports CSP protocol v1 and v2 at runtime. The Docker image uses Linux because recent libcsp releases do not provide maintained native macOS support. See [versioning policy](docs/versioning.md) for the supported combinations.

## Local development

```sh
git submodule update --init --recursive
uv sync --locked
uv run --locked csp-lab up --protocol 2 --build
uv run --locked csp-lab ping 2 --json
uv run --locked csp-lab doctor --json
uv run --locked csp-lab down
```

`--build` compiles the native image from this checkout, so it needs the libcsp submodule. Without `--build`, the CLI uses the image matching its own version from GHCR. That image becomes available after the first release.

Once released, the package can run without a clone or local compilation:

```sh
uvx csp-lab up --protocol 2
uvx csp-lab ping 2
uvx csp-lab down
```

You can also install it with `uv tool install csp-lab` and then use `csp-lab` directly. Lab state is stored in `~/.local/state/csp-lab` or under `XDG_STATE_HOME` when set, so commands work from any directory.

If a lab was started by the earlier checkout-based CLI, run `csp-lab down` from that checkout once before switching to `uvx` from another directory. The CLI recognizes and removes the earlier path-based Docker project there.

To test CSP v1, start a fresh lab with `up --protocol 1`. `up` refuses to change the protocol of an existing lab; run `down` first. The two versions never share a running network.

The CLI commands are `up`, `down`, `status`, `topology`, `ping <address>`, and `doctor`. `status` checks whether the hub and both nodes are running in Docker. Use `--json` for machine-readable output. `ping` and `doctor` return a nonzero exit code when a node does not respond. Addresses must be 1–31 for CSP v1 or 1–16383 for CSP v2. The demo nodes use addresses 2 and 3; the probe uses address 4.

## MCP

Start the lab first, then configure an MCP client to launch `csp-lab mcp` over stdio. After the package is published, the entry is:

```json
{
  "mcpServers": {
    "csp-lab": {
      "command": "uvx",
      "args": ["csp-lab", "mcp"]
    }
  }
}
```

With `uv tool install`, set the command to `csp-lab` and the argument to `mcp`. GUI clients may not see the shell's `PATH`; use the full path to `uvx` or the installed `csp-lab` executable when needed. For a local clone after `uv sync`, use `/absolute/path/to/csp-lab/.venv/bin/csp-lab` as the command. Keep the lab running in a separate terminal while using the MCP tools.

The stdio server provides `csp_topology`, `csp_ping`, and `csp_diagnose`. It does not expose commands that reboot, shut down, or read or write node memory. The simulated server binds only the CSP ping service port.

## What is tested

`uv run --locked pytest` checks protocol validation, unavailable Docker, cleanup after a failed start, and unresponsive nodes without requiring Docker. Run `uv run --locked ruff check src/csp_lab tests` and `uv run --locked mypy src/csp_lab` for lint and types. The Docker integration flow exercises real libcsp, ZMQ, CLI diagnostics, and MCP for both protocol versions:

```sh
bash test/integration.sh
```

The GitHub Actions workflow is configured to run both checks on Ubuntu with Docker Compose. The local integration script stops the lab on exit, including after a failure.

## Scope and license

This lab uses the pinned libcsp release as a build dependency and selects CSP v1 or v2 as a runtime protocol. It does not build against historical libcsp 1.x releases or operate mixed-version networks. ZMQ is the only transport in this lab. The project is licensed under [MIT](LICENSE); the libcsp submodule retains its own license. Image component notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
