"""Docker Compose orchestration and CSP probe operations."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from contextlib import ExitStack
from importlib.metadata import version
from importlib.resources import as_file, files
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
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
STATE_DIR = STATE_HOME / "csp-lab"
STATE_FILE = STATE_DIR / "state.json"
PROJECT = "csp-lab"
IMAGE = "ghcr.io/lackim/csp-lab"
OWNED_PROJECT = re.compile(r"csp-lab-py-[0-9a-f]{16}\Z")


def selected_image(build: bool) -> str:
    return "csp-lab-native:local" if build else f"{IMAGE}:v{version('csp-lab')}"


def project_image(project: str, build: bool) -> str:
    return f"csp-lab-native:{project}" if build else selected_image(False)


def state_image(state: LabState) -> str:
    return state.image or selected_image(state.build)


def state_project(state: LabState) -> str:
    return state.project or PROJECT


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


def _read_state(path: Path) -> LabState | None:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return LabState.model_validate_json(content)
    except ValidationError as exc:
        raise ValueError("Unsupported lab state. Run 'csp-lab down' and start again.") from exc


def get_state() -> LabState | None:
    state = _read_state(STATE_FILE)
    if state is not None:
        return state
    for root in dict.fromkeys((ROOT, Path.cwd().resolve())):
        legacy = _read_state(root / ".csp-lab" / "state.json")
        if legacy is not None:
            project = "csp-lab-" + hashlib.sha256(str(root).encode()).hexdigest()[:8]
            return legacy.model_copy(
                update={
                    "build": True,
                    "image": "csp-lab-native:local",
                    "project": project,
                    "legacy_root": root,
                }
            )
    return None


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


def compose(
    args: list[str],
    protocol: Protocol,
    image: str | None = None,
    timeout: int = 120,
    build_overlay: bool = False,
    project: str = PROJECT,
) -> str:
    env = {"CSP_VERSION": str(protocol), "CSP_LAB_IMAGE": image or selected_image(False)}
    with ExitStack() as stack:
        compose_file = stack.enter_context(as_file(files("csp_lab") / "data" / "compose.yaml"))
        file_args = ["-f", str(compose_file)]
        if build_overlay:
            if not (ROOT / "Dockerfile").is_file() or not (ROOT / "vendor" / "libcsp").is_dir():
                raise RuntimeError(
                    "Local build requires a source checkout with the libcsp submodule."
                )
            build_file = stack.enter_context(
                as_file(files("csp_lab") / "data" / "compose.build.yaml")
            )
            file_args.extend(["-f", str(build_file)])
            env["CSP_LAB_BUILD_CONTEXT"] = str(ROOT)
        return run("docker", ["compose", "-p", project, *file_args, *args], env, timeout)


def start_project(protocol: Protocol, image: str, project: str, build: bool = False) -> None:
    if build and (not (ROOT / "Dockerfile").is_file() or not (ROOT / "vendor" / "libcsp").is_dir()):
        raise RuntimeError("Local build requires a source checkout with the libcsp submodule.")
    pull = ["--build"] if build else ["--pull", "always"]
    compose(
        ["up", "-d", *pull, "hub", "node2", "node3"],
        protocol,
        image,
        600,
        build_overlay=build,
        project=project,
    )
    for target in (2, 3):
        if not ping_with_protocol(target, protocol, image, project).reachable:
            raise RuntimeError(f"Node {target} did not answer a CSP v{protocol} ping.")


def up(protocol: Protocol, build: bool = False) -> LabState:
    if get_state() is not None:
        raise RuntimeError("Lab is already started. Run 'csp-lab down' before changing protocol.")
    image = selected_image(build)
    try:
        start_project(protocol, image, PROJECT, build)
        state = LabState(
            protocol=protocol,
            libcspVersion="2.1",
            build=build,
            image=image,
            project=PROJECT,
        )
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(
                {**state.as_json(), "build": build, "image": image, "project": PROJECT},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return state
    except Exception:
        try:
            compose(["down", "--remove-orphans"], protocol, image)
        except Exception:
            pass
        shutil.rmtree(STATE_DIR, ignore_errors=True)
        raise


def down() -> None:
    state = get_state()
    compose(
        ["down", "--remove-orphans"],
        state.protocol if state else 2,
        state_image(state) if state else None,
        project=state_project(state) if state else PROJECT,
    )
    state_dir = state.legacy_root / ".csp-lab" if state and state.legacy_root else STATE_DIR
    shutil.rmtree(state_dir, ignore_errors=True)


def cleanup_owned_project(project: str) -> None:
    if OWNED_PROJECT.fullmatch(project) is None:
        raise ValueError("Expected a csp-lab-py project name with 16 lowercase hex digits.")
    errors: list[str] = []
    try:
        compose(["down", "--remove-orphans"], 2, project=project)
    except Exception as exc:
        errors.append(f"Compose cleanup failed: {exc}")
    image = project_image(project, True)
    try:
        tags = run(
            "docker",
            [
                "image",
                "ls",
                "--filter",
                f"reference={image}",
                "--format",
                "{{.Repository}}:{{.Tag}}",
            ],
            timeout=30,
        ).splitlines()
        if image in tags:
            run("docker", ["image", "rm", image], timeout=120)
    except Exception as exc:
        errors.append(f"Image cleanup failed: {exc}")
    if errors:
        raise RuntimeError(
            f"Could not fully clean {project}. Run 'csp-lab cleanup {project}' to retry. "
            + " ".join(errors)
        )


def ping_with_protocol(
    target: int, protocol: Protocol, image: str | None = None, project: str = PROJECT
) -> PingResult:
    parse_address(str(target), protocol)
    output = compose(
        ["run", "--rm", "--no-deps", "probe", "probe", str(protocol), "4", str(target)],
        protocol,
        image,
        30,
        project=project,
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
    return ping_with_protocol(target, state.protocol, state_image(state), state_project(state))


def topology_for_protocol(protocol: Protocol) -> TopologyResult:
    return TopologyResult(
        protocol=protocol,
        libcspVersion="2.1",
        nodes=[2, 3],
        probeAddress=4,
        transport="ZMQ",
    )


def topology() -> TopologyResult:
    state = require_state()
    return topology_for_protocol(state.protocol)


def diagnose_project(protocol: Protocol, image: str, project: str) -> DiagnoseResult:
    nodes = [ping_with_protocol(target, protocol, image, project) for target in (2, 3)]
    return DiagnoseResult(
        protocol=protocol,
        libcspVersion="2.1",
        healthy=all(node.reachable for node in nodes),
        nodes=nodes,
    )


def diagnose() -> DiagnoseResult:
    state = require_state()
    return diagnose_project(state.protocol, state_image(state), state_project(state))


def status() -> StatusResult:
    state = get_state()
    if state is None:
        return StatusResult(running=False, state=None)
    services = compose(
        ["ps", "--services", "--status", "running"],
        state.protocol,
        state_image(state),
        project=state_project(state),
    ).splitlines()
    running = all(name in services for name in ("hub", "node2", "node3"))
    return StatusResult(running=running, state=state)
