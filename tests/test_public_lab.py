"""Public Lab ownership, lifecycle, and recovery behavior."""

import re

import pytest
from typer.testing import CliRunner

from csp_lab import Lab, docker
from csp_lab.cli import app
from csp_lab.models import PingResult


def test_two_labs_are_isolated_and_return_existing_models(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[int, str, str, bool]] = []
    cleaned: list[str] = []
    probes: list[tuple[int, int, str]] = []

    def start(protocol: int, image: str, project: str, build: bool) -> None:
        started.append((protocol, image, project, build))

    def probe(target: int, protocol: int, image: str, project: str) -> PingResult:
        probes.append((target, protocol, project))
        return PingResult(target=target, reachable=True, rttMs=2)

    monkeypatch.setattr(docker, "start_project", start)
    monkeypatch.setattr(docker, "cleanup_owned_project", cleaned.append)
    monkeypatch.setattr(docker, "ping_with_protocol", probe)

    with Lab(protocol=1) as first, Lab(protocol=2) as second:
        assert first.project != second.project
        assert re.fullmatch(r"csp-lab-py-[0-9a-f]{16}", first.project)
        ping = first.ping(2)
        assert ping.reachable
        assert ping.rtt_ms == 2
        assert first.topology().nodes == [2, 3]
        assert second.diagnose().healthy
        assert probes == [(2, 1, first.project), (2, 2, second.project), (3, 2, second.project)]

    assert [item[2] for item in started] == [first.project, second.project]
    assert all(item[1] == "ghcr.io/lackim/csp-lab:v0.1.0" for item in started)
    assert cleaned == [second.project, first.project]


def test_invalid_inputs_and_lifecycle_do_not_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="Protocol must be 1 or 2"):
        Lab(protocol=3)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Build must be a boolean"):
        Lab(build="yes")  # type: ignore[arg-type]

    probes: list[int] = []
    monkeypatch.setattr(docker, "start_project", lambda *args: None)
    monkeypatch.setattr(docker, "cleanup_owned_project", lambda *args: None)
    monkeypatch.setattr(docker, "ping_with_protocol", lambda *args: probes.append(1))
    lab = Lab(protocol=1)
    with pytest.raises(RuntimeError, match="Enter its context"):
        lab.ping(2)
    with lab:
        with pytest.raises(ValueError, match="between 1 and 31"):
            lab.ping(32)
    with pytest.raises(RuntimeError, match="Enter its context"):
        lab.diagnose()
    with pytest.raises(RuntimeError, match="cannot be entered again"):
        with lab:
            pass
    assert not probes


def test_start_failure_keeps_primary_error_and_attempts_cleanup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cleaned: list[str] = []

    def fail_start(*args: object) -> None:
        raise RuntimeError("probe failed")

    def fail_cleanup(project: str) -> None:
        cleaned.append(project)
        raise RuntimeError(f"Could not fully clean {project}. Run 'csp-lab cleanup {project}'.")

    monkeypatch.setattr(docker, "start_project", fail_start)
    monkeypatch.setattr(docker, "cleanup_owned_project", fail_cleanup)
    lab = Lab()
    with pytest.raises(RuntimeError, match="probe failed"):
        with lab:
            pass
    assert cleaned == [lab.project]
    assert f"csp-lab cleanup {lab.project}" in capsys.readouterr().err


def test_unresponsive_start_cleans_only_its_project(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def compose(args: list[str], protocol: int, *remaining: object, **kwargs: object) -> str:
        calls.append((args[0], str(kwargs["project"])))
        return ""

    monkeypatch.setattr(docker, "compose", compose)
    monkeypatch.setattr(
        docker,
        "ping_with_protocol",
        lambda target, protocol, image, project: PingResult(
            target=target, reachable=False, rttMs=-1
        ),
    )
    monkeypatch.setattr(docker, "run", lambda *args, **kwargs: "")
    lab = Lab()
    with pytest.raises(RuntimeError, match="Node 2 did not answer"):
        with lab:
            pass
    assert calls == [("up", lab.project), ("down", lab.project)]


def test_body_error_stays_primary_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(docker, "start_project", lambda *args: None)

    def fail_cleanup(project: str) -> None:
        raise RuntimeError(f"Could not fully clean {project}. Run 'csp-lab cleanup {project}'.")

    monkeypatch.setattr(docker, "cleanup_owned_project", fail_cleanup)
    lab = Lab()
    with pytest.raises(ValueError, match="test body"):
        with lab:
            raise ValueError("test body")
    assert f"csp-lab cleanup {lab.project}" in capsys.readouterr().err

    with pytest.raises(RuntimeError, match="Could not fully clean"):
        with Lab():
            pass


def test_owned_cleanup_removes_only_its_project_and_image(monkeypatch: pytest.MonkeyPatch) -> None:
    project = "csp-lab-py-0123456789abcdef"
    image = f"csp-lab-native:{project}"
    calls: list[tuple[str, object]] = []

    def compose(args: list[str], protocol: int, **kwargs: object) -> str:
        calls.append(("compose", kwargs["project"]))
        raise RuntimeError("down failed")

    def run(command: str, args: list[str], **kwargs: object) -> str:
        calls.append(("docker", args))
        if args[:2] == ["image", "ls"]:
            return image + "\n"
        return ""

    monkeypatch.setattr(docker, "compose", compose)
    monkeypatch.setattr(docker, "run", run)
    with pytest.raises(RuntimeError, match="down failed"):
        docker.cleanup_owned_project(project)
    assert calls[0] == ("compose", project)
    assert ("docker", ["image", "rm", image]) in calls

    result = CliRunner().invoke(app, ["cleanup", "csp-lab", "--json"])
    assert result.exit_code == 1
    assert calls.count(("compose", project)) == 1
