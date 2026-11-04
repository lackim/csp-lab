"""Bounded, read-only snapshots of one lab's service status and logs."""

import json
import os
import re
import secrets
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
MANIFEST_RESERVE = 64 * 1024


@dataclass
class _Output:
    data: bytes
    truncated: bool = False
    error: str | None = None


def _capture(args: list[str], deadline: float, limit: int) -> _Output:
    """Bound bytes while reading, even when a single Docker log line is huge."""
    output = bytearray()
    if time.monotonic() >= deadline - 0.2:
        return _Output(b"", error="deadline")
    process = subprocess.Popen(
        ["docker", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if args[:2] == ["container", "logs"] else subprocess.DEVNULL,
        start_new_session=True,
    )
    assert process.stdout is not None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - 0.2 - time.monotonic()
                if remaining <= 0:
                    return _Output(bytes(output), truncated=True, error="deadline")
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, min(65536, limit + 1 - len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > limit:
                        return _Output(bytes(output[:limit]), truncated=True)
        remaining = deadline - 0.2 - time.monotonic()
        if remaining <= 0:
            return _Output(bytes(output), error="deadline")
        try:
            status = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            return _Output(bytes(output), error="deadline")
        return _Output(bytes(output), error="exit" if status else None)
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        process.stdout.close()


def collect_bundle(
    root: Path,
    project: str,
    protocol: int,
    addresses: tuple[int, ...],
    images: dict[int, str],
    operation: dict[str, str | int],
    error: BaseException | None,
) -> Path:
    deadline = time.monotonic() + 5
    destination = root / project / secrets.token_hex(8)
    destination.mkdir(parents=True, exist_ok=False)
    allowed = {"hub", "node2", "node3", *(f"custom{address}" for address in addresses)}
    containers: list[dict[str, object]] = []
    logs: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    truncated = False

    def capture(phase: str, args: list[str], limit: int) -> _Output:
        nonlocal truncated
        try:
            result = _capture(args, deadline, limit)
        except Exception as exc:
            result = _Output(b"", error=type(exc).__name__[:128])
        if result.error:
            failures.append({"phase": phase, "error": result.error})
        truncated |= result.truncated
        return result

    listing = capture(
        "containers",
        [
            "container",
            "ls",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        8192,
    )
    # Select fields at the daemon: never fetch environments, commands, or full
    # inspect output. Probe stdout is deliberately excluded from all snapshots.
    template = (
        '{"id":{{json .Id}},"imageId":{{json .Image}},'
        '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"status":{{json .State.Status}},"exitCode":{{json .State.ExitCode}}}'
    )
    remaining_bytes = MAX_BYTES - MANIFEST_RESERVE
    listed_ids = listing.data.splitlines()
    truncated |= len(listed_ids) > 32
    for raw_id in listed_ids[:32]:
        container_id = raw_id.decode("ascii", errors="replace")
        if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
            failures.append({"phase": "containers", "error": "invalid-id"})
            continue
        inspected = capture(
            "status", ["container", "inspect", "--format", template, container_id], 4096
        )
        try:
            metadata = json.loads(inspected.data)
        except (ValueError, UnicodeError):
            failures.append({"phase": "status", "error": "invalid-json"})
            continue
        if not isinstance(metadata, dict) or (
            metadata.get("project") != project
            or type(metadata.get("service")) is not str
            or metadata.get("id") != container_id
        ):
            continue
        if metadata["service"] not in allowed:
            continue
        if (
            set(metadata) != {"id", "imageId", "project", "service", "status", "exitCode"}
            or type(metadata.get("imageId")) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", metadata["imageId"]) is None
            or metadata.get("status")
            not in ("created", "running", "paused", "restarting", "removing", "exited", "dead")
            or type(metadata.get("exitCode")) is not int
        ):
            failures.append({"phase": "status", "error": "invalid-metadata"})
            continue
        service = str(metadata["service"])
        containers.append(metadata)
        if remaining_bytes <= 0:
            truncated = True
            break
        captured = capture(
            "logs", ["container", "logs", "--tail", "200", container_id], remaining_bytes
        )
        filename = f"{service}-{container_id[:12]}.log"
        (destination / filename).write_bytes(captured.data)
        remaining_bytes -= len(captured.data)
        logs.append(
            {"file": filename, "bytes": len(captured.data), "truncated": captured.truncated}
        )
    manifest = {
        "project": project,
        "protocol": protocol,
        "addresses": sorted((2, 3, *addresses)),
        "imageIds": images,
        "exception": type(error).__name__[:128] if error is not None else None,
        "operation": operation,
        "containers": containers,
        "logs": logs,
        "truncated": truncated,
        "captureErrors": failures,
    }
    encoded = (json.dumps(manifest, indent=2) + "\n").encode()
    if len(encoded) > MANIFEST_RESERVE:
        # Metadata is already bounded per command and container; retain a
        # minimal manifest if an unexpected daemon response consumes the reserve.
        manifest["containers"] = []
        manifest["captureErrors"] = [{"phase": "manifest", "error": "metadata-limit"}]
        manifest["truncated"] = True
        encoded = (json.dumps(manifest, indent=2) + "\n").encode()
    (destination / "manifest.json").write_bytes(encoded)
    return destination.resolve()
