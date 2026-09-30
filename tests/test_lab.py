"""CLI and orchestration behavior without a running Docker daemon."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from csp_lab import docker
from csp_lab.cli import app

runner = CliRunner()


@pytest.fixture
def lab_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(docker, "ROOT", tmp_path)
    monkeypatch.setattr(docker, "STATE_DIR", tmp_path / ".csp-lab")
    monkeypatch.setattr(docker, "STATE_FILE", tmp_path / ".csp-lab" / "state.json")
    (tmp_path / "bin").mkdir()
    return tmp_path


def put_state(root: Path, protocol: int = 2) -> None:
    state_dir = root / ".csp-lab"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"protocol": protocol, "libcspVersion": "2.1"}), encoding="utf-8"
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
        assert result.stdout == "0.1.0\n"


def test_docker_unavailable(lab_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put_state(lab_root)
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 1
    assert "ENOENT" in json.loads(result.stdout)["error"]


def test_failed_startup_cleans_up(lab_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_docker(
        lab_root,
        'printf "%s\\n" "$*" >> "$CSP_LAB_FAKE_LOG"\n'
        'for arg do if [ "$arg" = up ]; then exit 17; fi; done\n',
    )
    log = lab_root / "docker.log"
    monkeypatch.setenv("PATH", str(lab_root / "bin"))
    monkeypatch.setenv("CSP_LAB_FAKE_LOG", str(log))
    result = runner.invoke(app, ["up", "--protocol", "2", "--json"])
    assert result.exit_code == 1
    assert "docker exited 17" in json.loads(result.stdout)["error"]
    calls = log.read_text(encoding="utf-8")
    assert "up -d --build hub node2 node3" in calls
    assert "down --remove-orphans" in calls
    assert not (lab_root / ".csp-lab" / "state.json").exists()


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
