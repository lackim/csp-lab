"""CLI and orchestration behavior without a running Docker daemon."""

import hashlib
import json
from importlib.metadata import version
from pathlib import Path

import pytest
from typer.testing import CliRunner

from csp_lab import docker
from csp_lab.cli import app

runner = CliRunner()


@pytest.fixture
def lab_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(docker, "ROOT", tmp_path)
    monkeypatch.setattr(docker, "STATE_DIR", tmp_path / "state" / "csp-lab")
    monkeypatch.setattr(docker, "STATE_FILE", tmp_path / "state" / "csp-lab" / "state.json")
    (tmp_path / "bin").mkdir()
    return tmp_path


def put_state(root: Path, protocol: int = 2, build: bool = False, image: str | None = None) -> None:
    state_dir = root / "state" / "csp-lab"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        json.dumps({"protocol": protocol, "libcspVersion": "2.1", "build": build, "image": image}),
        encoding="utf-8",
    )


def fake_docker(root: Path, script: str) -> None:
    path = root / "bin" / "docker"
    path.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    path.chmod(0o755)


def test_protocol_and_address_validation() -> None:
    assert docker.parse_protocol("1") == 1
    assert docker.parse_protocol("2") == 2
    with pytest.raises(ValueError, match="Protocol must be 1 or 2"):
        docker.parse_protocol("3")
    assert docker.parse_address("31", 1) == 31
    assert docker.parse_address("16383", 2) == 16383
    with pytest.raises(ValueError, match="between 1 and 31"):
        docker.parse_address("32", 1)
    with pytest.raises(ValueError, match="between 1 and 16383"):
        docker.parse_address("0", 2)
    with pytest.raises(ValueError, match="decimal integer"):
        docker.parse_address("2foo", 2)


def test_status_when_not_started(lab_root: Path) -> None:
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"running": False, "state": None}


def test_global_options_remain_compatible(lab_root: Path) -> None:
    result = runner.invoke(app, ["--json", "status"])
    assert result.exit_code == 0
    assert result.stdout == '{"running":false,"state":null}\n'
    for args in (["--version"], ["status", "--version"], ["-V"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert result.stdout == f"{version('csp-lab')}\n"


def test_docker_unavailable(lab_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put_state(lab_root)
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 1
    assert "ENOENT" in json.loads(result.stdout)["error"]


def test_failed_startup_cleans_up(lab_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_docker(
        lab_root,
        'printf "%s|%s\\n" "$CSP_LAB_IMAGE" "$*" >> "$CSP_LAB_FAKE_LOG"\n'
        'for arg do if [ "$arg" = up ]; then exit 17; fi; done\n',
    )
    log = lab_root / "docker.log"
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    monkeypatch.setenv("CSP_LAB_FAKE_LOG", str(log))
    result = runner.invoke(app, ["up", "--protocol", "2", "--json"])
    assert result.exit_code == 1
    assert "docker exited 17" in json.loads(result.stdout)["error"]
    calls = log.read_text(encoding="utf-8")
    assert f"ghcr.io/lackim/csp-lab:v{version('csp-lab')}|compose -p csp-lab" in calls
    assert "up -d --pull always hub node2 node3" in calls
    assert "down --remove-orphans" in calls
    assert not (lab_root / "state" / "csp-lab" / "state.json").exists()


def test_local_build_uses_source_checkout(lab_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (lab_root / "Dockerfile").touch()
    (lab_root / "vendor" / "libcsp").mkdir(parents=True)
    fake_docker(
        lab_root,
        'printf "%s|%s|%s\\n" "$CSP_LAB_IMAGE" "$CSP_LAB_BUILD_CONTEXT" '
        '"$*" >> "$CSP_LAB_FAKE_LOG"\n'
        'for arg do if [ "$arg" = up ]; then exit 17; fi; done\n',
    )
    log = lab_root / "docker.log"
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    monkeypatch.setenv("CSP_LAB_FAKE_LOG", str(log))
    result = runner.invoke(app, ["up", "--protocol", "2", "--build", "--json"])
    assert result.exit_code == 1
    calls = log.read_text(encoding="utf-8")
    assert f"csp-lab-native:local|{lab_root}|compose -p csp-lab" in calls
    assert "compose.build.yaml up -d --build hub node2 node3" in calls
    assert "down --remove-orphans" in calls


def test_local_lab_can_be_stopped_without_checkout(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_state(lab_root, build=True)
    fake_docker(lab_root, 'printf "%s|%s\\n" "$CSP_LAB_IMAGE" "$*" >> "$CSP_LAB_FAKE_LOG"\n')
    log = lab_root / "docker.log"
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    monkeypatch.setenv("CSP_LAB_FAKE_LOG", str(log))
    result = runner.invoke(app, ["down", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"stopped": True}
    assert not (lab_root / "state" / "csp-lab" / "state.json").exists()
    calls = log.read_text(encoding="utf-8")
    assert "csp-lab-native:local|compose -p csp-lab" in calls
    assert "compose.build.yaml" not in calls


def test_running_lab_keeps_its_image_after_cli_upgrade(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_state(lab_root, image="ghcr.io/lackim/csp-lab:v0.1.0")
    fake_docker(
        lab_root,
        'printf "%s\\n" "$CSP_LAB_IMAGE" >> "$CSP_LAB_FAKE_LOG"\n'
        'for argument do if [ "$argument" = ps ]; then '
        'printf "hub\\nnode2\\nnode3\\n"; fi; done\n',
    )
    monkeypatch.setattr(docker, "version", lambda _: "0.2.0")
    log = lab_root / "docker.log"
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    monkeypatch.setenv("CSP_LAB_FAKE_LOG", str(log))
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["running"] is True
    assert log.read_text(encoding="utf-8") == "ghcr.io/lackim/csp-lab:v0.1.0\n"


def test_legacy_lab_can_be_seen_and_stopped(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_dir = lab_root / ".csp-lab"
    legacy_dir.mkdir()
    (legacy_dir / "state.json").write_text(
        '{"protocol":2,"libcspVersion":"2.1"}', encoding="utf-8"
    )
    fake_docker(
        lab_root,
        'printf "%s|%s\\n" "$CSP_LAB_IMAGE" "$*" >> "$CSP_LAB_FAKE_LOG"\n'
        'for argument do if [ "$argument" = ps ]; then '
        'printf "hub\\nnode2\\nnode3\\n"; fi; done\n',
    )
    log = lab_root / "docker.log"
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    monkeypatch.setenv("CSP_LAB_FAKE_LOG", str(log))
    expected_project = "csp-lab-" + hashlib.sha256(str(lab_root).encode()).hexdigest()[:8]
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["running"] is True
    result = runner.invoke(app, ["down", "--json"])
    assert result.exit_code == 0
    assert not legacy_dir.exists()
    calls = log.read_text(encoding="utf-8")
    assert f"csp-lab-native:local|compose -p {expected_project}" in calls


def test_unresponsive_node_sets_exit_code(lab_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put_state(lab_root)
    fake_docker(
        lab_root,
        'for arg do target=$arg; done\n'
        'case "$target" in\n'
        '  2) printf "%s\\n" \'{"target":2,"reachable":true,"rttMs":3}\' ;;\n'
        '  3) printf "%s\\n" \'{"target":3,"reachable":false,"rttMs":-1}\' ;;\n'
        'esac\n',
    )
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    result = runner.invoke(app, ["ping", "3", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"target": 3, "reachable": False, "rttMs": -1}
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["healthy"] is False
    assert [(node["target"], node["reachable"]) for node in report["nodes"]] == [
        (2, True),
        (3, False),
    ]


def test_bad_probe_result_is_rejected(lab_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put_state(lab_root)
    fake_docker(lab_root, "printf '%s\\n' '{\"target\":2,\"reachable\":\"yes\",\"rttMs\":3}'\n")
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    result = runner.invoke(app, ["ping", "2", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"error": "Probe returned an invalid result."}
