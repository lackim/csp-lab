"""Isolated, context-managed CSP lab for Python callers."""

import os
import secrets
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Literal

from csp_lab import artifacts, docker
from csp_lab.models import DiagnoseResult, PingResult, Protocol, TopologyResult
from csp_lab.nodes import Node, normalize_nodes
from csp_lab.requests import validate_request


class Lab:
    """Own one Docker Compose project until its context exits."""

    protocol: Protocol
    build: bool
    project: str
    image: str
    _active: bool
    _closed: bool
    _probes: docker.ProbeOwner

    def __init__(
        self,
        protocol: Protocol = 2,
        build: bool = False,
        *,
        nodes: Sequence[Node] = (),
        artifacts_dir: str | os.PathLike[str] | None = "artifacts/csp-lab",
    ) -> None:
        if type(protocol) is not int or protocol not in (1, 2):
            raise ValueError("Protocol must be 1 or 2.")
        if type(build) is not bool:
            raise ValueError("Build must be a boolean.")
        if artifacts_dir is not None and (
            not isinstance(artifacts_dir, (str, os.PathLike))
            or not isinstance(os.fspath(artifacts_dir), str)
            or not os.fspath(artifacts_dir)
        ):
            raise ValueError("Artifacts directory must be a nonempty path or None.")
        self.protocol = protocol
        self.build = build
        self._nodes: tuple[Node, ...] = normalize_nodes(nodes, protocol)
        self.project = f"csp-lab-py-{secrets.token_hex(8)}"
        self.image = docker.project_image(self.project, build)
        self._active = False
        self._closed = False
        self._probes = docker.ProbeOwner(self.project)
        self._images: dict[int, str] = {}
        self._configuration: tempfile.TemporaryDirectory[str] | None = None
        self._compose_file: Path | None = None
        self.artifacts_dir: Path | None = Path(artifacts_dir) if artifacts_dir is not None else None
        self._artifact_count: int = 0
        self._captured_context_error: BaseException | None = None
        self._operation: dict[str, str | int] = {"name": "startup"}

    @property
    def nodes(self) -> tuple[Node, ...]:
        return self._nodes

    @property
    def _addresses(self) -> tuple[int, ...]:
        return tuple(node.address for node in self.nodes)

    def __enter__(self) -> "Lab":
        if self._active or self._closed:
            raise RuntimeError("This Lab context cannot be entered again; create a new Lab.")
        try:
            if self.nodes:
                self._images = docker.resolve_node_images(self.nodes)
                self._configuration = tempfile.TemporaryDirectory(prefix=f"{self.project}-")
                self._compose_file = Path(self._configuration.name) / "nodes.json"
                docker.write_node_overlay(self._compose_file, self.protocol, self._images)
                try:
                    docker.start_custom_project(
                        self.protocol,
                        self.image,
                        self.project,
                        self.build,
                        self._compose_file,
                        self._addresses,
                        self._probes,
                    )
                except Exception as exc:
                    images = ", ".join(f"{node.address}={node.image!r}" for node in self.nodes)
                    raise RuntimeError(f"Custom-node startup failed ({images}): {exc}") from exc
            else:
                docker.start_project(self.protocol, self.image, self.project, self.build)
        except BaseException as exc:
            self._closed = True
            self.collect_artifacts(exc)
            try:
                docker.cleanup_owned_project(self.project)
            except Exception as cleanup_error:
                self._report_cleanup_error(cleanup_error)
            self._discard_configuration()
            raise
        self._active = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if not self._active:
            raise RuntimeError("Lab is not started.")
        self._active = False
        self._closed = True
        if exc is not None and self._captured_context_error is not exc:
            self.collect_artifacts(exc)
        try:
            docker.cleanup_owned_project(self.project)
        except Exception as cleanup_error:
            if exc is not None:
                self._report_cleanup_error(cleanup_error)
            else:
                self._operation = {"name": "cleanup"}
                self.collect_artifacts(cleanup_error)
                raise
        finally:
            self._captured_context_error = None
            self._discard_configuration()
        return False

    def _discard_configuration(self) -> None:
        if self._configuration is not None:
            try:
                self._configuration.cleanup()
            except Exception as exc:
                self._report_cleanup_error(exc)
            self._configuration = None
            self._compose_file = None

    def _report_cleanup_error(self, error: Exception) -> None:
        try:
            sys.stderr.write(f"csp-lab: {error}\n")
        except Exception:
            pass

    def collect_artifacts(self, error: BaseException | None = None) -> Path | None:
        """Save a bounded snapshot, including when probe cleanup poisoned the lab.

        Logs are verbatim application output and may contain sensitive data.
        Collection failure never replaces the caller's original exception.
        """
        if self.artifacts_dir is None or self._artifact_count >= 20:
            return None
        self._artifact_count += 1
        try:
            saved = artifacts.collect_bundle(
                self.artifacts_dir,
                self.project,
                self.protocol,
                self._addresses,
                self._images,
                self._operation.copy(),
                error,
            )
            sys.stderr.write(f"csp-lab: failure artifacts saved to {saved}\n")
            return saved
        except BaseException as exc:
            try:
                sys.stderr.write(f"csp-lab: artifact capture failed ({type(exc).__name__}).\n")
            except Exception:
                pass
            if error is None and not isinstance(exc, Exception):
                raise
            return None

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("Lab is not started. Enter its context first.")
        self._probes.require_usable()

    def ping(self, address: int) -> PingResult:
        self._require_active()
        if type(address) is not int:
            raise ValueError("Address must be an integer.")
        docker.parse_address(str(address), self.protocol)
        self._operation = {"name": "ping", "address": address}
        return docker.ping_with_protocol(
            address, self.protocol, self.image, self.project, owner=self._probes
        )

    def topology(self) -> TopologyResult:
        self._require_active()
        return docker.topology_for_protocol(self.protocol, self._addresses)

    def diagnose(self) -> DiagnoseResult:
        self._require_active()
        self._operation = {"name": "diagnose"}
        return docker.diagnose_project(
            self.protocol, self.image, self.project, owner=self._probes, images=self._images
        )

    def _custom_address(self, address: int) -> None:
        if type(address) is not int or address not in self._addresses:
            raise ValueError("Address must identify a configured custom node.")

    def request(self, address: int, *, port: int, payload: bytes, timeout_ms: int = 1500) -> bytes:
        """Send once and return one raw reply, including a possible empty packet.

        A timeout does not prove the service did not execute the request. Include
        application request IDs if late replies need transaction correlation.
        """
        self._require_active()
        self._custom_address(address)
        validate_request(port, payload, timeout_ms)
        self._operation = {
            "name": "request",
            "address": address,
            "port": port,
            "timeoutMs": timeout_ms,
        }
        return docker.request_with_protocol(
            address, port, payload, timeout_ms, self.protocol, self.image, self._probes
        )

    def stop_node(self, address: int) -> None:
        """Stop one custom container without removing it or changing topology."""
        self._require_active()
        self._custom_address(address)
        self._operation = {"name": "stop_node", "address": address}
        assert self._compose_file is not None
        docker.change_node_state(
            address, False, self.protocol, self.image, self.project, self._compose_file
        )

    def start_node(self, address: int) -> None:
        """Start the original container and wait for its CSP ping readiness."""
        self._require_active()
        self._custom_address(address)
        self._operation = {"name": "start_node", "address": address}
        assert self._compose_file is not None
        docker.change_node_state(
            address, True, self.protocol, self.image, self.project, self._compose_file
        )
        docker.wait_for_nodes((address,), self.protocol, self.image, self.project, self._probes)
