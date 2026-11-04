"""Bounded snapshots preserve failures, ownership, and data minimization."""

import json
import os
import sys
import time

import pytest

from csp_lab import Lab, Node, artifacts, docker


def test_manifest_selects_metadata_and_service_logs_only(tmp_path, monkeypatch):
    project = "csp-lab-py-0123456789abcdef"
    ids = [character * 64 for character in "abc"]
    calls = []

    def capture(args, deadline, limit):
        calls.append(args)
        assert 0 < deadline - time.monotonic() <= 5
        if args[:2] == ["container", "ls"]:
            assert f"label=com.docker.compose.project={project}" in args
            metadata = [
                {
                    "id": container,
                    "image": "sha256:" + "a" * 64,
                    "project": "foreign" if container == ids[2] else project,
                    "service": "probe" if container == ids[1] else "custom5",
                    "status": "running",
                }
                for container in ids
            ]
            return artifacts._Output("\n".join(json.dumps(row) for row in metadata).encode())
        assert args == ["container", "logs", "--tail", "200", ids[0]]
        return artifacts._Output(b"verbatim app output including app-data\n")

    monkeypatch.setattr(artifacts, "_capture", capture)
    bundle = artifacts.collect_bundle(
        tmp_path,
        project,
        2,
        (5,),
        {5: "sha256:" + "a" * 64},
        {"name": "request", "address": 5, "port": 10, "timeoutMs": 1500},
        AssertionError("payload-and-assertion-secret"),
    )
    assert bundle.is_absolute()
    manifest_text = (bundle / "manifest.json").read_text()
    manifest = json.loads(manifest_text)
    assert manifest["addresses"] == [2, 3, 5]
    assert manifest["exception"] == "AssertionError"
    assert manifest["operation"]["port"] == 10
    assert [item["service"] for item in manifest["containers"]] == ["custom5"]
    assert "payload-and-assertion-secret" not in manifest_text
    assert "app-data" not in manifest_text
    assert len(list(bundle.glob("*.log"))) == 1
    assert next(bundle.glob("*.log")).read_bytes() == b"verbatim app output including app-data\n"
    assert all(args[:2] in (["container", "ls"], ["container", "logs"]) for args in calls)
    assert all(".Env" not in " ".join(args) and ".Command" not in " ".join(args) for args in calls)
    assert not manifest["captureErrors"] and not manifest["truncated"]


def test_total_file_bytes_are_bounded_even_for_one_huge_line(tmp_path, monkeypatch):
    project = "csp-lab-py-0123456789abcdef"
    identity = "a" * 64

    def capture(args, deadline, limit):
        if args[:2] == ["container", "ls"]:
            return artifacts._Output(
                json.dumps(
                    {
                        "id": identity,
                        "image": "sha256:" + identity,
                        "project": project,
                        "service": "hub",
                        "status": "running",
                    }
                ).encode()
            )
        return artifacts._Output(b"x" * limit, truncated=True)

    monkeypatch.setattr(artifacts, "_capture", capture)
    bundle = artifacts.collect_bundle(tmp_path, project, 2, (), {}, {}, RuntimeError("secret"))
    assert sum(path.stat().st_size for path in bundle.iterdir()) <= 2 * 1024 * 1024
    assert json.loads((bundle / "manifest.json").read_text())["truncated"] is True


def test_capture_failures_are_recorded_without_exception_text(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("must-not-persist-this-error")

    monkeypatch.setattr(artifacts, "_capture", fail)
    bundle = artifacts.collect_bundle(tmp_path, "project", 1, (), {}, {}, None)
    content = (bundle / "manifest.json").read_text()
    assert "must-not-persist" not in content
    assert json.loads(content)["captureErrors"] == [
        {"phase": "containers", "error": "RuntimeError"}
    ]


@pytest.mark.parametrize("failure", ["startup", "body"])
@pytest.mark.parametrize("capture_failure", [False, True, "interrupt"])
def test_lab_captures_before_cleanup_without_masking_primary(
    tmp_path,
    monkeypatch,
    capsys,
    failure,
    capture_failure,
):
    events = []
    original = ValueError("primary error")

    def start(*args):
        if failure == "startup":
            raise original

    def capture(*args):
        events.append("capture")
        if capture_failure == "interrupt":
            raise KeyboardInterrupt()
        if capture_failure:
            raise OSError("capture secret")
        assert args[-1] is original
        return tmp_path

    monkeypatch.setattr(docker, "start_project", start)
    monkeypatch.setattr(docker, "cleanup_owned_project", lambda project: events.append("cleanup"))
    monkeypatch.setattr(artifacts, "collect_bundle", capture)
    with pytest.raises(ValueError) as raised:
        with Lab(artifacts_dir=tmp_path):
            raise original
    assert raised.value is original
    assert events == ["capture", "cleanup"]
    assert "capture secret" not in capsys.readouterr().err


def test_disabled_capture_expected_timeout_and_twenty_bundle_limit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(docker, "start_project", lambda *args: None)
    monkeypatch.setattr(docker, "cleanup_owned_project", lambda project: None)
    monkeypatch.setattr(artifacts, "collect_bundle", lambda *args: calls.append(args) or tmp_path)
    with pytest.raises(ValueError):
        with Lab(artifacts_dir=None):
            raise ValueError("disabled")
    assert not calls
    with Lab(artifacts_dir=tmp_path) as lab:
        # An expected/caught exception never leaves the context.
        with pytest.raises(TimeoutError):
            raise TimeoutError()
    assert not calls
    with Lab(artifacts_dir=tmp_path) as lab:
        lab._probes._failure = "unusable"
        for _ in range(25):
            lab.collect_artifacts(ValueError("metadata excludes this"))
    assert len(calls) == 20


@pytest.mark.parametrize("directory", [False, 1, b"path", ""])
def test_invalid_artifact_directory_fails_before_docker(directory, monkeypatch):
    calls = []
    monkeypatch.setattr(docker, "run", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="directory"):
        Lab(artifacts_dir=directory)
    assert not calls


def test_request_metadata_has_no_payload(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(docker, "resolve_node_images", lambda nodes: {5: "sha256:" + "a" * 64})
    monkeypatch.setattr(docker, "start_custom_project", lambda *args: None)
    monkeypatch.setattr(docker, "cleanup_owned_project", lambda project: None)
    monkeypatch.setattr(docker, "request_with_protocol", lambda *args: b"wrong")
    monkeypatch.setattr(artifacts, "collect_bundle", lambda *args: calls.append(args) or tmp_path)
    with pytest.raises(AssertionError):
        with Lab(nodes=(Node(5, "app"),), artifacts_dir=tmp_path) as lab:
            assert lab.request(5, port=10, payload=b"secret") == b"expected"
    assert calls[0][-2] == {"name": "request", "address": 5, "port": 10, "timeoutMs": 1500}


def test_bounded_reader_captures_stderr_and_kills_owned_cli_on_limit(tmp_path, monkeypatch):
    executable = tmp_path / "docker"
    executable.write_text(
        f"#!{sys.executable}\nimport os, time\nos.write(2, b'x' * 1000000)\ntime.sleep(30)\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    started = time.monotonic()
    result = artifacts._capture(["container", "logs"], started + 5, 100)
    assert result.data == b"x" * 100
    assert result.truncated
    assert time.monotonic() - started < 5


def test_bounded_reader_preserves_partial_output_on_deadline(tmp_path, monkeypatch):
    executable = tmp_path / "docker"
    executable.write_text(
        f"#!{sys.executable}\nimport os, time\nos.write(1, b'partial')\ntime.sleep(30)\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    started = time.monotonic()
    result = artifacts._capture(["container", "logs"], started + 1, 100)
    assert result.data == b"partial"
    assert result.error == "deadline"
    assert time.monotonic() - started < 2


def test_cleanup_failure_can_be_snapshotted_without_replacing_it(tmp_path, monkeypatch):
    events = []
    original = RuntimeError("cleanup failed")
    monkeypatch.setattr(docker, "start_project", lambda *args: None)

    def cleanup(project):
        events.append("cleanup")
        raise original

    def capture(*args):
        assert args[-1] is original
        assert args[-2] == {"name": "cleanup"}
        events.append("capture")
        return tmp_path

    monkeypatch.setattr(docker, "cleanup_owned_project", cleanup)
    monkeypatch.setattr(artifacts, "collect_bundle", capture)
    with pytest.raises(RuntimeError) as raised:
        with Lab(artifacts_dir=tmp_path):
            pass
    assert raised.value is original
    assert events == ["cleanup", "capture"]
