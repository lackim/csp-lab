"""Custom node configuration, image resolution, and lifecycle contracts."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from csp_lab import Lab, Node, docker
from csp_lab.models import PingResult


@pytest.mark.parametrize(
    "kwargs",
    [
        {"address": True, "image": "app"},
        {"address": "5", "image": "app"},
        {"address": 0, "image": "app"},
        {"address": 16383, "image": "app"},
        {"address": 2, "image": "app"},
        {"address": 3, "image": "app"},
        {"address": 4, "image": "app"},
        {"address": 5, "image": " "},
        {"address": 5, "image": None},
    ],
)
def test_node_rejects_invalid_fields(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Node(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "nodes",
    [None, "image", [5], [Node(5, "a"), Node(5, "b")], tuple(Node(i, "a") for i in range(5, 14))],
)
def test_bad_node_configuration_never_calls_docker(nodes: object, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(docker, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError):
        Lab(nodes=nodes)  # type: ignore[arg-type]
    assert calls == []


def test_configuration_is_frozen_copied_sorted_and_protocol_bounded() -> None:
    source = [Node(30, "last"), Node(1, "first")]
    lab = Lab(protocol=1, nodes=source)
    source.clear()
    assert lab.nodes == (Node(1, "first"), Node(30, "last"))
    with pytest.raises(FrozenInstanceError):
        lab.nodes[0].image = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="CSP v1"):
        Lab(protocol=1, nodes=(Node(31, "app"),))
    assert Lab(protocol=2, nodes=(Node(16382, "app"),)).nodes[0].address == 16382


def test_resolution_uses_local_ids_and_only_pulls_missing_images(monkeypatch: pytest.MonkeyPatch):
    calls = []
    pulled = False
    identity = "sha256:" + "a" * 64

    def run(command, args, **kwargs):
        nonlocal pulled
        calls.append(args)
        if args[:2] == ["image", "pull"]:
            pulled = True
            return ""
        if args[-1] == "missing" and not pulled:
            raise RuntimeError("No such image")
        return identity + "\n"

    monkeypatch.setattr(docker, "run", run)
    nodes = (Node(5, "local"), Node(6, "local"), Node(7, "missing"))
    assert docker.resolve_node_images(nodes) == dict.fromkeys((5, 6, 7), identity)
    assert [args for args in calls if args[:2] == ["image", "pull"]] == [
        ["image", "pull", "missing"]
    ]
    assert sum(args[-1] == "local" for args in calls) == 1


def test_image_failure_cleans_project_without_creating_containers(monkeypatch: pytest.MonkeyPatch):
    cleaned = []
    calls = []

    def fail(command, args, **kwargs):
        calls.append(args)
        raise RuntimeError("invalid reference")

    monkeypatch.setattr(docker, "run", fail)
    monkeypatch.setattr(docker, "cleanup_owned_project", cleaned.append)
    lab = Lab(nodes=(Node(5, "bad image"),), artifacts_dir=None)
    with pytest.raises(RuntimeError, match="Could not prepare.*bad image"):
        with lab:
            pass
    assert all(args[0] == "image" for args in calls)
    assert cleaned == [lab.project]


def test_custom_context_keeps_overlay_and_validates_stop_start_before_docker(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = []
    overlay = None
    ids = {5: "sha256:" + "a" * 64}
    monkeypatch.setattr(docker, "resolve_node_images", lambda nodes: ids)
    monkeypatch.setattr(
        docker, "cleanup_owned_project", lambda project: calls.append(("down", project))
    )

    def start(protocol, image, project, build, extra_file, addresses, owner):
        nonlocal overlay
        overlay = extra_file
        services = json.loads(extra_file.read_text())["services"]
        assert set(services) == {"custom5"}
        config = services["custom5"]
        assert config["image"] == ids[5]
        assert set(config) == {"image", "depends_on", "environment"}
        assert config["environment"] == {
            "CSP_LAB_PROTOCOL": "2",
            "CSP_LAB_ADDRESS": "5",
            "CSP_LAB_ZMQ_PUBLISH_ENDPOINT": "tcp://hub:6000",
            "CSP_LAB_ZMQ_SUBSCRIBE_ENDPOINT": "tcp://hub:7000",
        }
        assert addresses == (5,)
        assert owner.project == project

    def change(address, start, protocol, image, project, extra_file):
        assert extra_file.exists()
        calls.append(("start" if start else "stop", address))

    def ping(target, *args, **kwargs):
        calls.append(("ping", target))
        return PingResult(target=target, reachable=True, rttMs=1)

    monkeypatch.setattr(docker, "start_custom_project", start)
    monkeypatch.setattr(docker, "change_node_state", change)
    monkeypatch.setattr(docker, "ping_with_protocol", ping)
    monkeypatch.setattr(
        docker,
        "ping_nodes",
        lambda targets, *args, **kwargs: [ping(target) for target in targets],
    )
    with Lab(nodes=(Node(5, "app"),)) as lab:
        assert overlay.exists()
        assert lab.topology().nodes == [2, 3, 5]
        result = lab.diagnose()
        assert [node.target for node in result.nodes] == [2, 3, 5]
        assert result.image_ids == ids
        assert result.as_json()["imageIds"] == {5: ids[5]}
        for bad in (2, 3, 4, 6, True, "5"):
            before = calls.copy()
            with pytest.raises(ValueError, match="configured custom"):
                lab.stop_node(bad)
            with pytest.raises(ValueError, match="configured custom"):
                lab.start_node(bad)
            assert calls == before
        lab.stop_node(5)
        lab.stop_node(5)
        assert lab.topology().nodes == [2, 3, 5]
        lab.start_node(5)
        lab.start_node(5)
    assert not overlay.exists()
    assert calls[-1] == ("down", lab.project)
    assert calls.count(("stop", 5)) == calls.count(("start", 5)) == 2


def test_readiness_retries_only_pending_nodes_with_one_deadline(monkeypatch: pytest.MonkeyPatch):
    clock = [0.0]
    calls = []

    def ping(targets, *args, **kwargs):
        calls.append((targets, kwargs["timeout"]))
        clock[0] += 1
        return [
            PingResult(target=target, reachable=target == 2 or clock[0] >= 3, rttMs=1)
            for target in targets
        ]

    monkeypatch.setattr(docker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(docker.time, "sleep", lambda delay: None)
    monkeypatch.setattr(docker, "ping_nodes", ping)
    docker.wait_for_nodes((2, 5, 6), 2, "native", "project", docker.ProbeOwner("project"))
    # Each round is one probe for every pending node, not one probe per node.
    assert calls == [((2, 5, 6), 30), ((5, 6), 29), ((5, 6), 28)]


def test_one_probe_pings_several_nodes_and_validates_each_result(monkeypatch: pytest.MonkeyPatch):
    commands = []
    replies = [
        '{"target":2,"reachable":true,"rttMs":1}',
        '{"target":5,"reachable":false,"rttMs":-1}',
    ]

    def execute(self, command, image, timeout=30, **kwargs):
        commands.append((command, image, timeout))
        return "library log line\n" + "\n".join(replies) + "\n"

    monkeypatch.setattr(docker.ProbeOwner, "execute", execute)
    owner = docker.ProbeOwner("project")
    results = docker.ping_nodes((2, 5), 2, "native", "project", owner, timeout=7)
    assert [(result.target, result.reachable) for result in results] == [(2, True), (5, False)]
    assert commands == [(["probe", "2", "4", "2", "5"], "native", 7)]

    for bad in (replies[:1], replies[::-1], [*replies, replies[0]]):
        replies = bad
        with pytest.raises(ValueError, match="invalid result"):
            docker.ping_nodes((2, 5), 2, "native", "project", owner)
    commands.clear()
    with pytest.raises(ValueError, match="between 1 and 16 nodes"):
        docker.ping_nodes(tuple(range(5, 22)), 2, "native", "project", owner)
    with pytest.raises(ValueError, match="between 1 and 31"):
        docker.ping_nodes((2, 32), 1, "native", "project", owner)
    with pytest.raises(ValueError, match="owner does not match"):
        docker.ping_nodes((2,), 2, "native", "other", owner)
    assert commands == []


def test_failed_restart_does_not_close_lab(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(docker, "resolve_node_images", lambda nodes: {5: "sha256:" + "a" * 64})
    monkeypatch.setattr(docker, "start_custom_project", lambda *args: None)
    monkeypatch.setattr(docker, "change_node_state", lambda *args: None)
    monkeypatch.setattr(docker, "cleanup_owned_project", lambda project: None)
    monkeypatch.setattr(
        docker,
        "ping_with_protocol",
        lambda target, *args, **kwargs: PingResult(target=target, reachable=True, rttMs=1),
    )

    def fail(*args):
        raise RuntimeError("readiness failed")

    monkeypatch.setattr(docker, "wait_for_nodes", fail)
    with Lab(nodes=(Node(5, "app"),)) as lab:
        with pytest.raises(RuntimeError, match="readiness failed"):
            lab.start_node(5)
        assert lab.ping(2).reachable


def test_start_prepares_native_image_before_up_with_custom_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls = []
    monkeypatch.setattr(docker, "compose", lambda args, *pos, **kw: calls.append((args, kw)))
    monkeypatch.setattr(docker, "wait_for_nodes", lambda *args: calls.append(("wait", args)))
    overlay = tmp_path / "nodes.json"
    docker.start_custom_project(
        2, "native", "project", True, overlay, (5, 6), docker.ProbeOwner("project")
    )
    assert calls[0][0] == ["build", "hub"]
    assert calls[0][1]["build_overlay"] is True
    assert calls[1][0] == [
        "up",
        "-d",
        "--no-build",
        "--pull",
        "never",
        "hub",
        "node2",
        "node3",
        "custom5",
        "custom6",
    ]
    assert calls[1][1]["extra_file"] == overlay
    assert calls[2][0] == "wait"


def test_readiness_failure_has_one_total_budget(monkeypatch: pytest.MonkeyPatch):
    clock = [0.0]

    def ping(targets, *args, **kwargs):
        clock[0] += 15
        return [PingResult(target=target, reachable=False, rttMs=-1) for target in targets]

    monkeypatch.setattr(docker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(docker.time, "sleep", lambda delay: None)
    monkeypatch.setattr(docker, "ping_nodes", ping)
    with pytest.raises(RuntimeError, match=r"Nodes \[5\].*within 30s"):
        docker.wait_for_nodes((5,), 2, "native", "project", docker.ProbeOwner("project"))
    assert clock[0] == 30


def test_custom_startup_failure_reports_images_and_removes_temporary_configuration(
    monkeypatch: pytest.MonkeyPatch,
):
    overlay = None
    cleaned = []
    monkeypatch.setattr(docker, "resolve_node_images", lambda nodes: {5: "sha256:" + "a" * 64})

    def fail(*args):
        nonlocal overlay
        overlay = args[4]
        raise RuntimeError("exec format error")

    def cleanup(project):
        assert overlay.exists()
        cleaned.append(project)

    monkeypatch.setattr(docker, "start_custom_project", fail)
    monkeypatch.setattr(docker, "cleanup_owned_project", cleanup)
    lab = Lab(nodes=(Node(5, "incompatible:dev"),), artifacts_dir=None)
    with pytest.raises(RuntimeError, match="5='incompatible:dev'.*exec format error"):
        with lab:
            pass
    assert cleaned == [lab.project]
    assert not overlay.exists()


def test_managed_image_is_pulled_only_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = []
    local = True

    def run(command, args, **kwargs):
        calls.append(args[:2])
        if args[:2] == ["image", "inspect"] and not local and ["image", "pull"] not in calls:
            raise RuntimeError("No such image")
        return "sha256:" + "b" * 64 + "\n"

    monkeypatch.setattr(docker, "run", run)
    monkeypatch.setattr(docker, "compose", lambda *args, **kwargs: "")
    monkeypatch.setattr(docker, "wait_for_nodes", lambda *args: None)
    start = (2, "native", "project", False, tmp_path / "nodes.json", (5,))
    docker.start_custom_project(*start, docker.ProbeOwner("project"))
    assert calls == [["image", "inspect"]]

    calls.clear()
    local = False
    docker.start_custom_project(*start, docker.ProbeOwner("project"))
    assert calls == [["image", "inspect"], ["image", "pull"], ["image", "inspect"]]
