"""Isolated, context-managed CSP lab for Python callers."""

import secrets
import sys
from types import TracebackType
from typing import Literal

from csp_lab import docker
from csp_lab.models import DiagnoseResult, PingResult, Protocol, TopologyResult


class Lab:
    """Own one Docker Compose project until its context exits."""

    protocol: Protocol
    build: bool
    project: str
    image: str
    _active: bool
    _closed: bool

    def __init__(self, protocol: Protocol = 2, build: bool = False) -> None:
        if type(protocol) is not int or protocol not in (1, 2):
            raise ValueError("Protocol must be 1 or 2.")
        if type(build) is not bool:
            raise ValueError("Build must be a boolean.")
        self.protocol = protocol
        self.build = build
        self.project = f"csp-lab-py-{secrets.token_hex(8)}"
        self.image = docker.project_image(self.project, build)
        self._active = False
        self._closed = False

    def __enter__(self) -> "Lab":
        if self._active or self._closed:
            raise RuntimeError("This Lab context cannot be entered again; create a new Lab.")
        try:
            docker.start_project(self.protocol, self.image, self.project, self.build)
        except BaseException:
            self._closed = True
            try:
                docker.cleanup_owned_project(self.project)
            except Exception as cleanup_error:
                self._report_cleanup_error(cleanup_error)
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
        try:
            docker.cleanup_owned_project(self.project)
        except Exception as cleanup_error:
            if exc is not None:
                self._report_cleanup_error(cleanup_error)
            else:
                raise
        return False

    def _report_cleanup_error(self, error: Exception) -> None:
        try:
            sys.stderr.write(f"csp-lab: {error}\n")
        except Exception:
            pass

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("Lab is not started. Enter its context first.")

    def ping(self, address: int) -> PingResult:
        self._require_active()
        if type(address) is not int:
            raise ValueError("Address must be an integer.")
        docker.parse_address(str(address), self.protocol)
        return docker.ping_with_protocol(address, self.protocol, self.image, self.project)

    def topology(self) -> TopologyResult:
        self._require_active()
        return docker.topology_for_protocol(self.protocol)

    def diagnose(self) -> DiagnoseResult:
        self._require_active()
        return docker.diagnose_project(self.protocol, self.image, self.project)
