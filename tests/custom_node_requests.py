"""Real raw packets, timeout/send-count semantics, and hostile frame proof."""

import secrets
import subprocess

from custom_node_lifecycle import containers, docker

from csp_lab import Lab, Node, RequestTimeout


def expect_timeout(lab: Lab, port: int, payload: bytes = b"executed") -> None:
    try:
        lab.request(5, port=port, payload=payload, timeout_ms=500)
    except RequestTimeout as exc:
        assert (exc.address, exc.port, exc.timeout_ms) == (5, port, 500)
    else:
        raise AssertionError("Expected a network timeout, not a reply")
    assert (
        docker(
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={lab.project}",
            "--filter",
            "label=com.docker.compose.service=probe",
        )
        == ""
    )


def invalid_native_inputs(image: str, protocol: int) -> None:
    base = ["request", str(protocol), "4", "5", "10", "100"]
    cases = [
        (base, value)
        for value in ("0\n", "gg\n", "00 11\n", "00\n00\n", "00" * 257 + "\n", "00" * 65536 + "\n")
    ]
    for position, value in (
        (1, "3"),
        (2, "99999"),
        (3, "0"),
        (3, "4"),
        (3, "31" if protocol == 1 else "16383"),
        (4, "9"),
        (4, "64"),
        (4, "-1"),
        (4, "10x"),
        (5, "99"),
        (5, "10001"),
        (5, "9" * 100),
    ):
        args = base.copy()
        args[position] = value
        cases.append((args, "\n"))
    for args, payload in cases:
        name = "csp-lab-invalid-request-" + secrets.token_hex(8)
        try:
            result = subprocess.run(
                ["docker", "run", "--rm", "--name", name, "--network", "none", "-i", image, *args],
                input=payload,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            assert result.returncode == 2, (args, result.returncode, result.stderr)
            assert '"status"' not in result.stdout
        finally:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15)


def main() -> None:
    tag = "csp-lab-uppercase:requests-" + secrets.token_hex(8)
    try:
        docker(
            "build",
            "-f",
            "examples/custom-node/Dockerfile",
            "--build-arg",
            "TESTING=ON",
            "-t",
            tag,
            ".",
        )
        identity = docker("image", "inspect", "--format", "{{.Id}}", tag)
        with Lab(build=True) as unrelated:
            for protocol in (1, 2):
                with Lab(protocol=protocol, build=True, nodes=(Node(5, tag),)) as lab:
                    for payload in (b"", b"hello\x00world\xff", bytes(range(256))):
                        assert lab.request(5, port=10, payload=payload) == payload.upper()
                    assert lab.request(5, port=13, payload=b"nonempty") == b""
                    before = int.from_bytes(lab.request(5, port=11, payload=b""), "big")
                    expect_timeout(lab, 12)
                    after = int.from_bytes(lab.request(5, port=11, payload=b""), "big")
                    assert after == before + 1, ("application request was retried", before, after)
                    for port in (14, 15, 16):
                        expect_timeout(lab, port)
                        assert lab.ping(5).reachable and lab.ping(2).reachable
                    # Stopping affects only the custom node in this lab.
                    lab.stop_node(5)
                    expect_timeout(lab, 10)
                    assert lab.ping(2).reachable and unrelated.ping(3).reachable
                    lab.start_node(5)
                    nonce = secrets.token_bytes(8)
                    payload = b"hello:" + nonce
                    assert lab.request(5, port=10, payload=payload) == payload.upper()
                    invalid_native_inputs(lab.image, protocol)
                assert containers(lab.project) == ""
                assert docker("image", "inspect", "--format", "{{.Id}}", tag) == identity
                assert unrelated.ping(2).reachable
        print(
            "Raw requests passed: CSP v1/v2, binary bytes, empty reply, "
            "one send, bounds, filtering, restart"
        )
    finally:
        subprocess.run(
            ["docker", "image", "rm", tag], check=False, capture_output=True, timeout=120
        )


if __name__ == "__main__":
    main()
