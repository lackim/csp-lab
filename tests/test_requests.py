"""Public request limits, raw transport, strict results, and ownership."""

import json
import subprocess

import pytest
from test_probe_lifecycle import Daemon

from csp_lab import Lab, Node, RequestTimeout, docker


@pytest.fixture
def active_lab(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(docker, "resolve_node_images", lambda nodes: {5: "sha256:" + "a" * 64})
    monkeypatch.setattr(docker, "start_custom_project", lambda *args: None)
    monkeypatch.setattr(docker, "cleanup_owned_project", lambda project: None)
    with Lab(nodes=(Node(5, "app"),)) as lab:
        yield lab


@pytest.mark.parametrize(
    "change",
    [
        {"address": 2},
        {"address": 4},
        {"address": 6},
        {"address": True},
        {"address": "5"},
        {"port": 9},
        {"port": 64},
        {"port": True},
        {"port": 10.0},
        {"port": "10"},
        {"payload": "hello"},
        {"payload": bytearray(b"hello")},
        {"payload": b"a" * 257},
        {"payload": None},
        {"timeout_ms": 99},
        {"timeout_ms": 10001},
        {"timeout_ms": True},
        {"timeout_ms": 1500.0},
        {"timeout_ms": "1500"},
    ],
)
def test_bad_request_never_starts_a_probe(active_lab, monkeypatch, change):
    calls = []
    monkeypatch.setattr(docker.ProbeOwner, "execute", lambda *args, **kwargs: calls.append(args))
    arguments = {"address": 5, "port": 10, "payload": b"ok", "timeout_ms": 1500, **change}
    with pytest.raises(ValueError):
        active_lab.request(**arguments)
    assert calls == []


@pytest.mark.parametrize("payload,port,timeout", [(b"", 10, 100), (bytes(range(256)), 63, 10000)])
def test_raw_input_uses_stdin_and_validated_reply_bytes(
    active_lab, monkeypatch, payload, port, timeout
):
    calls = []

    def execute(owner, command, image, **kwargs):
        calls.append((command, kwargs))
        assert payload.hex() not in command
        assert kwargs["input_text"] == payload.hex() + "\n"
        return "library log\n" + json.dumps({"status": "ok", "reply": payload.hex()})

    monkeypatch.setattr(docker.ProbeOwner, "execute", execute)
    assert active_lab.request(5, port=port, payload=payload, timeout_ms=timeout) == payload
    assert calls == [
        (
            ["request", "2", "4", "5", str(port), str(timeout)],
            {"timeout": 30 + timeout / 1000, "input_text": payload.hex() + "\n"},
        )
    ]


@pytest.mark.parametrize(
    "result",
    [
        "",
        "payload-secret",
        '{"status":',
        '{"status":"ok","reply":null}',
        '{"status":"ok","reply":"0"}',
        '{"status":"ok","reply":"gg"}',
        '{"status":"ok","reply":"00 11"}',
        '{"status":"ok","reply":false}',
        json.dumps({"status": "ok", "reply": "00" * 257}),
        '{"status":"ok","reply":"","extra":true}',
        '{"status":"error"}',
        '{"status":"timeout","reply":""}',
        '{"status":"timeout"}\n{"status":"ok","reply":""}',
    ],
)
def test_invalid_probe_result_never_retries_or_leaks_output(active_lab, monkeypatch, result):
    calls = []

    def execute(*args, **kwargs):
        calls.append(args)
        return result

    monkeypatch.setattr(docker.ProbeOwner, "execute", execute)
    with pytest.raises(RuntimeError) as raised:
        active_lab.request(5, port=10, payload=b"payload-secret")
    assert "payload-secret" not in str(raised.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "outcome", ["reply", "network_timeout", "outer_timeout", "interrupt", "malformed"]
)
def test_request_cleanup_precedes_result_and_never_retries(active_lab, monkeypatch, outcome):
    daemon = Daemon()
    daemon.output = '{"status":"ok","reply":"00ff"}'
    received = []

    def run(command, args, **kwargs):
        received.append((args, kwargs.get("input_text")))
        return daemon.run(command, args, timeout=kwargs.get("timeout", 120))

    monkeypatch.setattr(docker, "run", run)
    if outcome == "network_timeout":
        daemon.output = '{"status":"timeout"}'
    elif outcome == "outer_timeout":
        daemon.start_failure = subprocess.TimeoutExpired("docker start", 30)
    elif outcome == "interrupt":
        daemon.start_failure = KeyboardInterrupt()
    elif outcome == "malformed":
        daemon.output = "broken"
    if outcome == "reply":
        assert active_lab.request(5, port=10, payload=b"sensitive\x00packet") == b"\x00\xff"
    else:
        error = {
            "network_timeout": RequestTimeout,
            "outer_timeout": RuntimeError,
            "interrupt": KeyboardInterrupt,
            "malformed": RuntimeError,
        }[outcome]
        with pytest.raises(error) as raised:
            active_lab.request(5, port=10, payload=b"sensitive\x00packet")
        if outcome == "network_timeout":
            assert isinstance(raised.value, TimeoutError)
            assert (raised.value.address, raised.value.port, raised.value.timeout_ms) == (
                5,
                10,
                1500,
            )
    assert not daemon.containers
    starts = [(args, stdin) for args, stdin in received if args[:2] == ["container", "start"]]
    assert len(starts) == 1 and starts[0][1] == b"sensitive\x00packet".hex() + "\n"
    assert all("sensitive" not in " ".join(args) for args, _ in received)
    assert all(b"sensitive\x00packet".hex() not in args for args, _ in received)
    active_lab._probes.require_usable()


def test_uncertain_cleanup_poisoning_blocks_requests_and_node_control(active_lab, monkeypatch):
    daemon = Daemon()
    daemon.output = '{"status":"ok","reply":""}'
    daemon.remove_failure = True
    monkeypatch.setattr(
        docker,
        "run",
        lambda command, args, **kwargs: daemon.run(
            command, args, timeout=kwargs.get("timeout", 120)
        ),
    )
    with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
        active_lab.request(5, port=10, payload=b"ok")
    before = len(daemon.calls)
    for operation in (
        lambda: active_lab.request(5, port=10, payload=b"ok"),
        lambda: active_lab.start_node(5),
        lambda: active_lab.stop_node(5),
    ):
        with pytest.raises(RuntimeError, match="unusable"):
            operation()
    assert len(daemon.calls) == before
