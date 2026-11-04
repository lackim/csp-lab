"""Enable pytest's isolated test-suite fixture."""

from pathlib import Path

import pytest

from csp_lab import docker

pytest_plugins = ("pytester",)


@pytest.fixture
def lab_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(docker, "ROOT", tmp_path)
    monkeypatch.setattr(docker, "STATE_DIR", tmp_path / "state" / "csp-lab")
    monkeypatch.setattr(docker, "STATE_FILE", tmp_path / "state" / "csp-lab" / "state.json")
    # These state/CLI tests fake Compose; daemon cleanup is exercised separately.
    monkeypatch.setattr(docker, "cleanup_project_probes", lambda project: None)
    (tmp_path / "bin").mkdir()
    return tmp_path
