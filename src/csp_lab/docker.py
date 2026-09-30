"""Docker Compose orchestration and CSP probe operations."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import ValidationError

from csp_lab.models import (
    DiagnoseResult,
    LabState,
    PingResult,
    Protocol,
    StatusResult,
    TopologyResult,
)

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / ".csp-lab"
STATE_FILE = STATE_DIR / "state.json"
PROJECT = "csp-lab-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]


def parse_protocol(value: str) -> Protocol:
    if value == "1":
        return 1
    if value == "2":
        return 2
    raise ValueError("Protocol must be 1 or 2.")


def parse_address(value: str, protocol: Protocol) -> int:
    if re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("Address must be a decimal integer.")
    address = int(value)
    maximum = 31 if protocol == 1 else 16383
    if address < 1 or address > maximum:
        raise ValueError(f"CSP v{protocol} address must be between 1 and {maximum}.")
    return address


def get_state() -> LabState | None:
    try:
        content = STATE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return LabState.model_validate_json(content)
    except ValidationError as exc:
        raise ValueError("Unsupported lab state. Run 'csp-lab down' and start again.") from exc


def require_state() -> LabState:
    state = get_state()
    if state is None:
        raise RuntimeError("Lab is not started. Run 'csp-lab up --protocol 2' first.")
    return state


def run(
    command: str, args: list[str], env: dict[str, str] | None = None, timeout: int = 120
) -> str:
    try:
        result = subprocess.run(
            [command, *args],
            cwd=ROOT,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{command} ENOENT: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{command} exited {result.returncode}: {detail}")
    return result.stdout


def compose(args: list[str], protocol: Protocol, timeout: int = 120) -> str:
    return run(
        "docker",
        ["compose", "-p", PROJECT, "-f", "compose.yaml", *args],
        {"CSP_VERSION": str(protocol)},
        timeout,
    )


def up(protocol: Protocol) -> LabState:
    if get_state() is not None:
        raise RuntimeError("Lab is already started. Run 'csp-lab down' before changing protocol.")
    try:
        compose(["up", "-d", "--build", "hub", "node2", "node3"], protocol, 600)
        for target in (2, 3):
            if not ping_with_protocol(target, protocol).reachable:
                raise RuntimeError(f"Node {target} did not answer a CSP v{protocol} ping.")
        state = LabState(protocol=protocol, libcspVersion="2.1")
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state.as_json(), indent=2) + "\n", encoding="utf-8")
        return state
    except Exception:
        try:
            compose(["down", "--remove-orphans"], protocol)
        except Exception:
            pass
        shutil.rmtree(STATE_DIR, ignore_errors=True)
        raise


def down() -> None:
    state = get_state()
    compose(["down", "--remove-orphans"], state.protocol if state else 2)
    shutil.rmtree(STATE_DIR, ignore_errors=True)


def ping_with_protocol(target: int, protocol: Protocol) -> PingResult:
    parse_address(str(target), protocol)
    output = compose(
        ["run", "--rm", "--no-deps", "probe", "probe", str(protocol), "4", str(target)],
        protocol,
        30,
    )
    line = next(
        (part for part in reversed(output.splitlines()) if part.startswith('{"target":')),
        None,
    )
    if line is None:
        raise ValueError(f"Probe returned no JSON result: {output.strip()}")
    try:
        result = PingResult.model_validate_json(line)
    except ValidationError as exc:
        raise ValueError("Probe returned an invalid result.") from exc
    if result.target != target:
        raise ValueError("Probe returned an invalid result.")
    return result


def ping(target: int) -> PingResult:
    state = require_state()
    return ping_with_protocol(target, state.protocol)


def topology() -> TopologyResult:
    state = require_state()
    return TopologyResult(
        protocol=state.protocol,
        libcspVersion=state.libcsp_version,
        nodes=[2, 3],
        probeAddress=4,
        transport="ZMQ",
    )


def diagnose() -> DiagnoseResult:
    state = require_state()
    nodes = [ping_with_protocol(target, state.protocol) for target in (2, 3)]
    return DiagnoseResult(
        protocol=state.protocol,
        libcspVersion=state.libcsp_version,
        healthy=all(node.reachable for node in nodes),
        nodes=nodes,
    )


def status() -> StatusResult:
    state = get_state()
    if state is None:
        return StatusResult(running=False, state=None)
    services = compose(["ps", "--services", "--status", "running"], state.protocol).splitlines()
    running = all(name in services for name in ("hub", "node2", "node3"))
    return StatusResult(running=running, state=state)
