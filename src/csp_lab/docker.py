"""Docker Compose orchestration and CSP probe operations."""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from importlib.metadata import version
from importlib.resources import as_file, files
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import ValidationError

from csp_lab.models import (
    DiagnoseResult,
    LabState,
    PingResult,
    Protocol,
    StatusResult,
    TopologyResult,
)
from csp_lab.nodes import Node

ROOT = Path(__file__).resolve().parents[2]
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
STATE_DIR = STATE_HOME / "csp-lab"
STATE_FILE = STATE_DIR / "state.json"
PROJECT = os.environ.get("CSP_LAB_PROJECT", "csp-lab")
IMAGE = "ghcr.io/lackim/csp-lab"
OWNED_PROJECT = re.compile(r"csp-lab-py-[0-9a-f]{16}\Z")


class _InvalidState(ValueError):
    def __init__(self, path: Path) -> None:
        super().__init__("Unsupported lab state. Run 'csp-lab down' and start again.")
        self.path = path


@contextmanager
def _state_lock() -> Iterator[None]:
    # Keep the lock outside STATE_DIR: down/rollback may remove that directory.
    lock_path = STATE_DIR.with_name(STATE_DIR.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_path, timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise RuntimeError(
            "Another csp-lab start/stop operation is in progress. Try again when it finishes."
        ) from exc
    try:
        yield
    finally:
        lock.release()


def _write_state(state: LabState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=STATE_DIR, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    **state.as_json(),
                    "build": state.build,
                    "image": state.image,
                    "project": state.project,
                },
                stream,
                indent=2,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, STATE_FILE)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _legacy_project(root: Path) -> str:
    return "csp-lab-" + hashlib.sha256(str(root).encode()).hexdigest()[:8]


def selected_image(build: bool) -> str:
    if not build:
        return f"{IMAGE}:v{version('csp-lab')}"
    return "csp-lab-native:local" if PROJECT == "csp-lab" else f"csp-lab-native:{PROJECT}"


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
    except UnicodeError as exc:
        raise _InvalidState(path) from exc
    try:
        return LabState.model_validate_json(content)
    except ValidationError as exc:
        raise _InvalidState(path) from exc


def get_state() -> LabState | None:
    state = _read_state(STATE_FILE)
    if state is not None:
        return state
    if PROJECT != "csp-lab":
        return None
    for root in dict.fromkeys((ROOT, Path.cwd().resolve())):
        legacy = _read_state(root / ".csp-lab" / "state.json")
        if legacy is not None:
            return legacy.model_copy(
                update={
                    "build": True,
                    "image": "csp-lab-native:local",
                    "project": _legacy_project(root),
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
    command: str, args: list[str], env: dict[str, str] | None = None, timeout: float = 120
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
    timeout: float = 120,
    build_overlay: bool = False,
    project: str = PROJECT,
    extra_file: Path | None = None,
) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project) is None:
        raise ValueError("Compose project must contain lowercase letters, digits, '_' or '-'.")
    env = {"CSP_VERSION": str(protocol), "CSP_LAB_IMAGE": image or selected_image(False)}
    with ExitStack() as stack:
        compose_file = stack.enter_context(as_file(files("csp_lab") / "data" / "compose.yaml"))
        file_args = ["-f", str(compose_file)]
        if extra_file is not None:
            file_args.extend(["-f", str(extra_file)])
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


def _docker_before(deadline: float, args: list[str]) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Probe cleanup exceeded five seconds.")
    return run("docker", args, timeout=remaining)


def _probe_ids(
    project: str,
    deadline: float,
    name: str | None = None,
    container: str | None = None,
) -> list[str]:
    args = ["container", "ls", "--all", "--quiet", "--no-trunc"]
    if container is not None:
        args.extend(["--filter", f"id={container}"])
    elif name is not None:
        args.extend(["--filter", f"name=^/{name}$"])
    else:
        args.extend(
            [
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=probe",
            ]
        )
    return _docker_before(deadline, args).splitlines()


def _remove_probe_id(container: str, project: str, deadline: float) -> None:
    labels = json.loads(
        _docker_before(
            deadline,
            [
                "container",
                "inspect",
                "--format",
                "{{json .Config.Labels}}",
                container,
            ],
        )
    )
    if not isinstance(labels, dict) or (
        labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != "probe"
    ):
        raise RuntimeError(
            "Refusing to remove a container without matching probe ownership labels."
        )
    _docker_before(deadline, ["container", "rm", "--force", container])


def cleanup_project_probes(project: str) -> None:
    """Remove daemon-owned one-offs that Compose may leave after a killed client."""
    deadline = time.monotonic() + 5
    for container in _probe_ids(project, deadline):
        _remove_probe_id(container, project, deadline)
    if _probe_ids(project, deadline):
        raise RuntimeError("Project probe removal could not be confirmed.")


class ProbeOwner:
    """Keep one lab unusable if an interrupted probe cannot be accounted for."""

    def __init__(self, project: str) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project) is None:
            raise ValueError("Invalid probe project name.")
        self.project = project
        self._failure: str | None = None

    def require_usable(self) -> None:
        if self._failure is not None:
            raise RuntimeError(self._failure)

    def _recovery(self) -> str:
        if OWNED_PROJECT.fullmatch(self.project):
            return f"csp-lab cleanup {self.project}"
        return "csp-lab down"

    def _finish(self, name: str, confirmed: str | None, primary: BaseException | None) -> None:
        try:
            deadline = time.monotonic() + 5
            containers = _probe_ids(self.project, deadline, name, confirmed)
            for container in containers:
                _remove_probe_id(container, self.project, deadline)
            if _probe_ids(self.project, deadline, name, confirmed):
                raise RuntimeError("Probe container is still present.")
            if not confirmed and not containers:
                # A timed-out create may still finish at the daemon. It cannot
                # send packets: only the separate, acknowledged start can do so.
                raise RuntimeError("Container creation was not acknowledged.")
        except BaseException as exc:
            self._failure = (
                f"Probe cleanup is unconfirmed for {self.project}; this lab is unusable. "
                f"Run '{self._recovery()}' to recover. {exc}"
            )
            if primary is not None and not isinstance(primary, Exception):
                try:
                    sys.stderr.write(self._failure + "\n")
                except Exception:
                    pass
            elif not isinstance(exc, Exception):
                try:
                    sys.stderr.write(self._failure + "\n")
                except Exception:
                    pass
                raise
            else:
                raise RuntimeError(self._failure) from (primary or exc)

    def execute(self, command: list[str], image: str, timeout: float = 30) -> str:
        self.require_usable()
        deadline = time.monotonic() + timeout
        try:
            networks = run(
                "docker",
                [
                    "network",
                    "ls",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={self.project}",
                    "--filter",
                    "label=com.docker.compose.network=default",
                ],
                timeout=min(10, timeout),
            ).splitlines()
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Probe network lookup exceeded its deadline for {self.project}."
            ) from exc
        if len(networks) != 1:
            raise RuntimeError("Expected one private Compose network for the lab.")
        name = f"{self.project}-probe-{secrets.token_hex(8)}"
        confirmed: str | None = None
        primary: BaseException | None = None
        try:
            container = run(
                "docker",
                [
                    "container",
                    "create",
                    "--name",
                    name,
                    "--interactive",
                    "--label",
                    f"com.docker.compose.project={self.project}",
                    "--label",
                    "com.docker.compose.service=probe",
                    "--label",
                    "com.docker.compose.oneoff=True",
                    "--network",
                    networks[0],
                    image,
                    *command,
                ],
                timeout=max(0.001, deadline - time.monotonic()),
            ).strip()
            if re.fullmatch(r"[0-9a-f]{64}", container) is None:
                raise RuntimeError("Docker returned an invalid probe container ID.")
            confirmed = container
            return run(
                "docker",
                ["container", "start", "--attach", "--interactive", container],
                timeout=max(0.001, deadline - time.monotonic()),
            )
        except subprocess.TimeoutExpired as exc:
            primary = RuntimeError(
                f"Probe command exceeded its deadline for {self.project}. "
                f"Run '{self._recovery()}' if recovery is needed."
            )
            raise primary from exc
        except BaseException as exc:
            primary = exc
            raise
        finally:
            self._finish(name, confirmed, primary)


def stop_project(protocol: Protocol, image: str | None, project: str) -> None:
    errors: list[str] = []
    try:
        cleanup_project_probes(project)
    except Exception as exc:
        errors.append(f"Probe cleanup failed: {exc}")
    try:
        compose(["down", "--remove-orphans"], protocol, image, project=project)
    except Exception as exc:
        errors.append(f"Compose cleanup failed: {exc}")
    if errors:
        raise RuntimeError(" ".join(errors))


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


def resolve_node_images(nodes: tuple[Node, ...]) -> dict[int, str]:
    """Use local images unchanged, pulling missing images before project creation."""
    resolved: dict[str, str] = {}
    for node in nodes:
        if node.image in resolved:
            continue
        try:
            try:
                identity = run("docker", ["image", "inspect", "--format", "{{.Id}}", node.image])
            except RuntimeError:
                run("docker", ["image", "pull", node.image], timeout=600)
                identity = run("docker", ["image", "inspect", "--format", "{{.Id}}", node.image])
            identity = identity.strip()
            if re.fullmatch(r"sha256:[0-9a-f]{64}", identity) is None:
                raise RuntimeError("Docker returned an invalid image ID.")
            resolved[node.image] = identity
        except Exception as exc:
            raise RuntimeError(
                f"Could not prepare custom node image {node.image!r}: {exc}"
            ) from exc
    return {node.address: resolved[node.image] for node in nodes}


def write_node_overlay(path: Path, protocol: Protocol, images: dict[int, str]) -> None:
    services = {
        f"custom{address}": {
            "image": identity,
            "depends_on": ["hub"],
            "environment": {
                "CSP_LAB_PROTOCOL": str(protocol),
                "CSP_LAB_ADDRESS": str(address),
                "CSP_LAB_ZMQ_PUBLISH_ENDPOINT": "tcp://hub:6000",
                "CSP_LAB_ZMQ_SUBSCRIBE_ENDPOINT": "tcp://hub:7000",
            },
        }
        for address, identity in images.items()
    }
    path.write_text(json.dumps({"services": services}), encoding="utf-8")


def wait_for_nodes(
    targets: tuple[int, ...], protocol: Protocol, image: str, project: str, owner: ProbeOwner
) -> None:
    deadline = time.monotonic() + 30
    pending = list(targets)
    while pending:
        for target in tuple(pending):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"Nodes {pending} did not answer CSP v{protocol} ping within 30s."
                )
            if ping_with_protocol(
                target, protocol, image, project, owner=owner, timeout=remaining
            ).reachable:
                pending.remove(target)
        if pending:
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def start_custom_project(
    protocol: Protocol,
    image: str,
    project: str,
    build: bool,
    extra_file: Path,
    addresses: tuple[int, ...],
    owner: ProbeOwner,
) -> None:
    # Prepare the native image separately so --pull cannot replace locally
    # resolved custom images, and the readiness budget excludes build/pull time.
    if build:
        compose(["build", "hub"], protocol, image, 600, build_overlay=True, project=project)
    else:
        run("docker", ["image", "pull", image], timeout=600)
    compose(
        [
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "hub",
            "node2",
            "node3",
            *(f"custom{address}" for address in addresses),
        ],
        protocol,
        image,
        project=project,
        extra_file=extra_file,
    )
    wait_for_nodes((2, 3, *addresses), protocol, image, project, owner)


def change_node_state(
    address: int, start: bool, protocol: Protocol, image: str, project: str, extra_file: Path
) -> None:
    # Compose start uses the existing container rather than recreating from a
    # mutable tag. A repeated stop/start is harmless.
    compose(
        ["start" if start else "stop", f"custom{address}"],
        protocol,
        image,
        project=project,
        extra_file=extra_file,
    )


def up(protocol: Protocol, build: bool = False) -> LabState:
    with _state_lock():
        if get_state() is not None:
            raise RuntimeError(
                "Lab is already started. Run 'csp-lab down' before changing protocol."
            )
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
            _write_state(state)
            return state
        except BaseException:
            try:
                stop_project(protocol, image, PROJECT)
            except Exception:
                pass
            shutil.rmtree(STATE_DIR, ignore_errors=True)
            raise


def down() -> None:
    with _state_lock():
        try:
            state = get_state()
        except _InvalidState as exc:
            state = None
            state_dir = exc.path.parent
            project = PROJECT if exc.path == STATE_FILE else _legacy_project(state_dir.parent)
        else:
            state_dir = state.legacy_root / ".csp-lab" if state and state.legacy_root else STATE_DIR
            project = state_project(state) if state else PROJECT
        stop_project(
            state.protocol if state else 2,
            state_image(state) if state else None,
            project,
        )
        shutil.rmtree(state_dir, ignore_errors=True)


def cleanup_owned_project(project: str) -> None:
    if OWNED_PROJECT.fullmatch(project) is None:
        raise ValueError("Expected a csp-lab-py project name with 16 lowercase hex digits.")
    errors: list[str] = []
    try:
        stop_project(2, None, project)
    except Exception as exc:
        errors.append(f"Project cleanup failed: {exc}")
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
    target: int,
    protocol: Protocol,
    image: str | None = None,
    project: str = PROJECT,
    *,
    owner: ProbeOwner | None = None,
    timeout: float = 30,
) -> PingResult:
    parse_address(str(target), protocol)
    if owner is not None and owner.project != project:
        raise ValueError("Probe owner does not match the requested project.")
    probe = owner if owner is not None else ProbeOwner(project)
    output = probe.execute(
        ["probe", str(protocol), "4", str(target)],
        image or selected_image(False),
        timeout=timeout,
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


def topology_for_protocol(protocol: Protocol, addresses: tuple[int, ...] = ()) -> TopologyResult:
    return TopologyResult(
        protocol=protocol,
        libcspVersion="2.1",
        nodes=sorted((2, 3, *addresses)),
        probeAddress=4,
        transport="ZMQ",
    )


def topology() -> TopologyResult:
    state = require_state()
    return topology_for_protocol(state.protocol)


def diagnose_project(
    protocol: Protocol,
    image: str,
    project: str,
    *,
    owner: ProbeOwner | None = None,
    images: dict[int, str] | None = None,
) -> DiagnoseResult:
    nodes = [
        ping_with_protocol(target, protocol, image, project, owner=owner)
        for target in sorted((2, 3, *(images or {})))
    ]
    return DiagnoseResult(
        protocol=protocol,
        libcspVersion="2.1",
        healthy=all(node.reachable for node in nodes),
        nodes=nodes,
        imageIds=images or {},
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
