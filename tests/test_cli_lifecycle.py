"""Recovery and exclusive ownership of CLI lifecycle transitions."""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from csp_lab import docker
from csp_lab.cli import app
from csp_lab.models import LabState


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("content", [b'{"protocol":', b'{"protocol":3}', b"\xff"])
def test_down_recovers_unreadable_state(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool, content: bytes
) -> None:
    state_file = lab_root / ".csp-lab" / "state.json" if legacy else docker.STATE_FILE
    state_file.parent.mkdir(parents=True)
    state_file.write_bytes(content)
    projects: list[str] = []

    def compose(*args: object, project: str, **kwargs: object) -> str:
        projects.append(project)
        return ""

    monkeypatch.setattr(docker, "compose", compose)
    runner = CliRunner()
    assert runner.invoke(app, ["status", "--json"]).exit_code == 1
    result = runner.invoke(app, ["down", "--json"])
    assert result.exit_code == 0, result.output
    expected = "csp-lab-" + hashlib.sha256(str(lab_root).encode()).hexdigest()[:8]
    assert projects == [expected if legacy else "csp-lab"]
    assert not state_file.exists()
    assert runner.invoke(app, ["status", "--json"]).exit_code == 0


def test_failed_recovery_preserves_state_for_retry(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker.STATE_DIR.mkdir(parents=True)
    docker.STATE_FILE.write_text("{truncated", encoding="utf-8")

    def fail_compose(*args: object, **kwargs: object) -> str:
        raise RuntimeError("Docker is unavailable")

    monkeypatch.setattr(docker, "compose", fail_compose)
    result = CliRunner().invoke(app, ["down", "--json"])
    assert result.exit_code == 1
    assert "Docker is unavailable" in result.stdout
    assert docker.STATE_FILE.read_text() == "{truncated"


def test_failed_atomic_replace_keeps_previous_valid_state(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = LabState(protocol=1, libcspVersion="2.1", project="csp-lab")
    docker._write_state(original)
    replacement = LabState(protocol=2, libcspVersion="2.1", project="csp-lab")

    def fail_replace(source: str, destination: Path) -> None:
        # Readers must still see the old complete state when publication fails.
        assert docker.get_state() == original
        raise OSError("replace failed")

    monkeypatch.setattr(docker.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        docker._write_state(replacement)
    assert docker.get_state() == original
    assert list(docker.STATE_DIR.iterdir()) == [docker.STATE_FILE]


def test_private_project_does_not_adopt_legacy_lab(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_file = lab_root / ".csp-lab" / "state.json"
    legacy_file.parent.mkdir()
    legacy_file.write_text('{"protocol":2,"libcspVersion":"2.1"}')
    monkeypatch.setattr(docker, "PROJECT", "csp-lab-test-private")
    projects: list[str] = []

    def compose(*args: object, project: str, **kwargs: object) -> str:
        projects.append(project)
        return ""

    monkeypatch.setattr(docker, "compose", compose)
    assert docker.get_state() is None
    assert docker.selected_image(True) == "csp-lab-native:csp-lab-test-private"
    docker.down()
    assert projects == ["csp-lab-test-private"]
    assert legacy_file.exists()


_LIFECYCLE_WORKER = """
import shutil
import sys
import time
from pathlib import Path
from csp_lab import docker

root, phase, protocol = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])

def block():
    (root / 'entered').touch()
    deadline = time.monotonic() + 20
    while not (root / 'release').exists():
        if time.monotonic() > deadline:
            raise RuntimeError('test worker timed out')
        time.sleep(0.01)

def start(*args):
    if phase == 'rollback':
        raise RuntimeError('startup failed')
    block()

original_rmtree = shutil.rmtree
def rmtree(*args, **kwargs):
    original_rmtree(*args, **kwargs)
    if phase in ('stop', 'rollback'):
        block()

docker.start_project = start
docker.compose = lambda *args, **kwargs: ''
docker.cleanup_project_probes = lambda project: None
docker.shutil.rmtree = rmtree
if phase == 'stop':
    docker.down()
else:
    docker.up(protocol)
"""


@pytest.mark.parametrize("phase", ["start", "stop", "rollback"])
def test_other_process_cannot_start_or_stop_during_transition(tmp_path: Path, phase: str) -> None:
    state_home = tmp_path / "state"
    state_file = state_home / "csp-lab" / "state.json"
    if phase == "stop":
        state_file.parent.mkdir(parents=True)
        state_file.write_text('{"protocol":1,"libcspVersion":"2.1"}')
    environment = {
        **os.environ,
        "XDG_STATE_HOME": str(state_home),
        "CSP_LAB_PROJECT": "csp-lab",
        "PYTHONDONTWRITEBYTECODE": "1",
        # A failing regression must never fall through to the real Docker CLI.
        "PATH": str(tmp_path / "no-docker"),
    }
    worker_args = [sys.executable, "-c", _LIFECYCLE_WORKER, str(tmp_path)]
    worker = subprocess.Popen(
        [*worker_args, phase, "1"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "entered").exists():
            assert worker.poll() is None, worker.communicate()
            assert time.monotonic() < deadline, "worker never entered the transition"
            time.sleep(0.01)
        for command in (["up", "--protocol", "2"], ["down"]):
            contender = subprocess.run(
                [sys.executable, "-m", "csp_lab.cli", *command, "--json"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert contender.returncode == 1
            assert "operation is in progress" in json.loads(contender.stdout)["error"]
    finally:
        (tmp_path / "release").touch()
        try:
            stdout, stderr = worker.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.communicate()
            raise
    assert worker.returncode == (1 if phase == "rollback" else 0), (stdout, stderr)
    if phase == "start":
        assert json.loads(state_file.read_text())["protocol"] == 1
        stopped = subprocess.run(
            [*worker_args, "stop", "1"],
            env=environment,
            capture_output=True,
            timeout=10,
        )
        assert stopped.returncode == 0, stopped.stderr
    else:
        assert not state_file.exists()
    # The lock must remain usable after down/rollback removes the state directory.
    restarted = subprocess.run(
        [*worker_args, "start", "2"],
        env=environment,
        capture_output=True,
        timeout=10,
    )
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(state_file.read_text())["protocol"] == 2


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_integration_failure_cleans_only_its_private_project(
    tmp_path: Path, cleanup_fails: bool
) -> None:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    fake_uv = binary_dir / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        'printf "%s|%s|%s\\n" "$CSP_LAB_PROJECT" "$XDG_STATE_HOME" "$*" >> "$TEST_UV_LOG"\n'
        'case "$*" in *" up "*) exit 17;; *" down"*) exit "$TEST_CLEANUP_EXIT";; esac\n'
    )
    fake_uv.chmod(0o755)
    user_state = tmp_path / "user-state"
    user_state.mkdir()
    sentinel = user_state / "existing-lab"
    sentinel.write_text("keep running")
    temporary_dir = tmp_path / "temporary"
    temporary_dir.mkdir()
    log_file = tmp_path / "uv.log"
    environment = {
        **os.environ,
        "PATH": str(binary_dir) + os.pathsep + os.environ["PATH"],
        "XDG_STATE_HOME": str(user_state),
        "CSP_LAB_PROJECT": "csp-lab",
        "TEST_UV_LOG": str(log_file),
        "TEST_CLEANUP_EXIT": "1" if cleanup_fails else "0",
        "TMPDIR": str(temporary_dir),
    }
    result = subprocess.run(
        ["bash", str(Path(__file__).resolve().parents[1] / "test" / "integration.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 17, result.stderr
    calls = [line.split("|", 2) for line in log_file.read_text().splitlines()]
    assert len(calls) == 3
    assert calls[0][0].startswith("csp-lab-py-")
    assert all(project == calls[0][0] for project, _, _ in calls)
    assert all(state != str(user_state) for _, state, _ in calls)
    assert calls[-2][2] == "run --locked csp-lab down"
    assert calls[-1][2] == "run --locked csp-lab cleanup " + calls[0][0]
    assert sentinel.read_text() == "keep running"
    if cleanup_fails:
        assert "Cleanup incomplete" in result.stderr
        assert Path(calls[0][1]).parent.exists()
    else:
        assert not list(temporary_dir.iterdir())
