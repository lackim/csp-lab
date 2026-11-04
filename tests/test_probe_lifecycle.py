"""Probe ownership across normal exit, outer failure, and cancellation."""

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from csp_lab import Lab, docker

PROJECT = "csp-lab-py-0123456789abcdef"


class Daemon:
    def __init__(self) -> None:
        self.containers: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[list[str], float]] = []
        self.start_failure: BaseException | None = None
        self.create_failure: BaseException | None = None
        self.remove_failure = False
        self.wrong_owner = False
        self.rename_on_start = False
        self.pending: dict[str, object] | None = None
        self.output = '{"target":2,"reachable":true,"rttMs":1}'

    def run(self, command: str, args: list[str], env: object = None, timeout: float = 120) -> str:
        assert command == "docker"
        assert timeout > 0
        self.calls.append((args, timeout))
        if args[:2] == ["network", "ls"]:
            return "private-network\n"
        if args[:2] == ["container", "create"]:
            labels = dict(
                args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "--label"
            )
            if self.wrong_owner:
                labels["com.docker.compose.project"] = "another-lab"
            self.pending = {"name": args[args.index("--name") + 1], "labels": labels}
            if self.create_failure is not None:
                raise self.create_failure
            self.containers["a" * 64] = self.pending
            return "a" * 64 + "\n"
        if args[:2] == ["container", "start"]:
            assert args[-1] in self.containers
            if self.rename_on_start:
                self.containers[args[-1]]["name"] = "renamed-after-create"
            if self.start_failure is not None:
                raise self.start_failure
            return self.output
        if args[:2] == ["container", "ls"]:
            filters = [args[i + 1] for i, arg in enumerate(args) if arg == "--filter"]
            selected = []
            for container, metadata in self.containers.items():
                matches = True
                for value in filters:
                    if value.startswith("name="):
                        matches &= re.fullmatch(value[5:], "/" + str(metadata["name"])) is not None
                    elif value.startswith("id="):
                        matches &= container == value[3:]
                    else:
                        key, expected = value[6:].split("=", 1)
                        matches &= metadata["labels"].get(key) == expected  # type: ignore[union-attr]
                if matches:
                    selected.append(container)
            return "\n".join(selected)
        if args[:2] == ["container", "inspect"]:
            return json.dumps(self.containers[args[-1]]["labels"])
        if args[:2] == ["container", "rm"]:
            if self.remove_failure:
                raise RuntimeError("daemon refused removal")
            del self.containers[args[-1]]
            return ""
        if args[:2] == ["image", "ls"]:
            return ""
        raise AssertionError(args)


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "interrupt", "malformed"])
def test_probe_removed_before_result_or_original_error(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    daemon = Daemon()
    monkeypatch.setattr(docker, "run", daemon.run)
    owner = docker.ProbeOwner(PROJECT)
    if outcome == "failure":
        daemon.start_failure = RuntimeError("start failed")
    elif outcome == "timeout":
        daemon.start_failure = subprocess.TimeoutExpired("docker start", 30)
    elif outcome == "interrupt":
        daemon.start_failure = KeyboardInterrupt()
    elif outcome == "malformed":
        daemon.output = "invalid reply"
    if outcome == "success":
        assert docker.ping_with_protocol(2, 2, project=PROJECT, owner=owner).reachable
    else:
        error = {
            "failure": RuntimeError,
            "timeout": RuntimeError,
            "interrupt": KeyboardInterrupt,
            "malformed": ValueError,
        }[outcome]
        with pytest.raises(error):
            docker.ping_with_protocol(2, 2, project=PROJECT, owner=owner)
    assert not daemon.containers
    assert sum(args[:2] == ["container", "start"] for args, _ in daemon.calls) == 1
    assert sum(args[:2] == ["container", "rm"] for args, _ in daemon.calls) == 1
    assert all(
        timeout <= 5
        for args, timeout in daemon.calls
        if args[:2]
        in (
            ["container", "ls"],
            ["container", "inspect"],
            ["container", "rm"],
        )
    )
    owner.require_usable()


def test_acknowledged_probe_is_removed_by_immutable_id(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = Daemon()
    daemon.rename_on_start = True
    monkeypatch.setattr(docker, "run", daemon.run)
    assert docker.ping_with_protocol(2, 2, project=PROJECT).reachable
    assert not daemon.containers


def test_unconfirmed_cleanup_disables_lab_but_allows_context_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = Daemon()
    daemon.remove_failure = True
    monkeypatch.setattr(docker, "run", daemon.run)
    monkeypatch.setattr(docker, "start_project", lambda *args: None)
    cleaned: list[str] = []
    monkeypatch.setattr(docker, "cleanup_owned_project", cleaned.append)
    with Lab() as lab:
        with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
            lab.ping(2)
        commands = len(daemon.calls)
        operations: list[Callable[[], object]] = [lambda: lab.ping(2), lab.diagnose, lab.topology]
        for operation in operations:
            with pytest.raises(RuntimeError, match=f"csp-lab cleanup {lab.project}"):
                operation()
        assert len(daemon.calls) == commands
    assert cleaned == [lab.project]


def test_cancel_preserves_exception_when_removal_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    daemon = Daemon()
    interrupt = KeyboardInterrupt()
    daemon.start_failure = interrupt
    daemon.remove_failure = True
    monkeypatch.setattr(docker, "run", daemon.run)
    owner = docker.ProbeOwner(PROJECT)
    with pytest.raises(KeyboardInterrupt) as raised:
        owner.execute(["probe", "2", "4", "2"], "native-image")
    assert raised.value is interrupt
    assert "csp-lab cleanup " + PROJECT in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="unusable"):
        owner.require_usable()


def test_foreign_container_is_never_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = Daemon()
    daemon.wrong_owner = True
    monkeypatch.setattr(docker, "run", daemon.run)
    owner = docker.ProbeOwner(PROJECT)
    with pytest.raises(RuntimeError, match="ownership labels"):
        owner.execute(["probe", "2", "4", "2"], "native-image")
    assert daemon.containers
    assert not any(args[:2] == ["container", "rm"] for args, _ in daemon.calls)


def test_timed_out_create_cannot_start_late_and_recovery_is_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = Daemon()
    daemon.create_failure = subprocess.TimeoutExpired("docker create", 30)
    monkeypatch.setattr(docker, "run", daemon.run)
    monkeypatch.setattr(docker, "compose", lambda *args, **kwargs: "")
    owner = docker.ProbeOwner(PROJECT)
    with pytest.raises(RuntimeError, match="not acknowledged"):
        owner.execute(["probe", "2", "4", "2"], "native-image")
    assert not any(args[:2] == ["container", "start"] for args, _ in daemon.calls)
    assert daemon.pending is not None
    # The daemon finishes creating a stopped container after the client timed out.
    daemon.containers["a" * 64] = daemon.pending
    daemon.containers["b" * 64] = {
        "name": "another-lab-probe",
        "labels": {
            "com.docker.compose.project": "another-lab",
            "com.docker.compose.service": "probe",
        },
    }
    with pytest.raises(RuntimeError, match="unusable"):
        owner.require_usable()
    docker.cleanup_owned_project(PROJECT)
    assert list(daemon.containers) == ["b" * 64]


def test_down_attempts_compose_even_if_probe_removal_fails(
    lab_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker.STATE_DIR.mkdir(parents=True)
    docker.STATE_FILE.write_text('{"protocol":2,"libcspVersion":"2.1"}')
    calls: list[str] = []

    def fail_probe_cleanup(project: str) -> None:
        raise RuntimeError("probe removal failed")

    monkeypatch.setattr(docker, "cleanup_project_probes", fail_probe_cleanup)
    monkeypatch.setattr(docker, "compose", lambda *args, **kwargs: calls.append("down"))
    with pytest.raises(RuntimeError, match="probe removal failed"):
        docker.down()
    assert calls == ["down"]
    assert docker.STATE_FILE.exists()
