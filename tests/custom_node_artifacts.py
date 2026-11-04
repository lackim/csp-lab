"""Real startup/readiness/body failures retain scoped artifacts after cleanup."""

import json
import secrets
import subprocess
import tempfile
from pathlib import Path

from custom_node_lifecycle import containers, docker

from csp_lab import Lab, Node, RequestTimeout


def manifest_for(root: Path, project: str) -> tuple[Path, dict]:
    found = list((root / project).rglob("manifest.json"))
    assert len(found) == 1, found
    path = found[0]
    assert containers(project) == ""
    assert sum(p.stat().st_size for p in path.parent.iterdir()) <= 2 * 1024 * 1024
    text = path.read_text()
    assert "assertion-secret" not in text and "payload-secret" not in text
    return path.parent, json.loads(text)


def main() -> None:
    token = secrets.token_hex(8)
    good = f"csp-lab-uppercase:artifacts-{token}"
    bad = f"csp-lab-no-ping:artifacts-{token}"
    missing = f"csp-lab-no-executable:artifacts-{token}"
    try:
        docker("build", "-f", "examples/custom-node/Dockerfile", "-t", good, ".")
        for tag, entrypoint in (
            (
                bad,
                [
                    "/bin/sh",
                    "-c",
                    "i=0; while [ $i -lt 300 ]; do echo line-$i; i=$((i+1)); done; exit 17",
                ],
            ),
            (missing, ["/no-such-application"]),
        ):
            subprocess.run(
                ["docker", "build", "-t", tag, "-"],
                input=f"FROM {good}\nENTRYPOINT {json.dumps(entrypoint)}\nCMD []\n",
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
        with tempfile.TemporaryDirectory(prefix="csp-lab-artifacts-proof-") as directory:
            root = Path(directory)
            with Lab(build=True, artifacts_dir=None) as unrelated:
                for image in (bad, missing):
                    lab = Lab(build=True, nodes=(Node(5, image),), artifacts_dir=root)
                    try:
                        with lab:
                            raise AssertionError("startup unexpectedly succeeded")
                    except RuntimeError:
                        pass
                    bundle, manifest = manifest_for(root, lab.project)
                    assert manifest["exception"] == "RuntimeError"
                    assert manifest["operation"] == {"name": "startup"}
                    assert manifest["imageIds"]["5"].startswith("sha256:")
                    if image == bad:
                        logs = list(bundle.glob("custom5-*.log"))
                        assert len(logs) == 1
                        lines = logs[0].read_text().splitlines()
                        assert lines == [f"line-{i}" for i in range(100, 300)], lines
                    assert unrelated.ping(2).reachable
                    docker("image", "inspect", image)

                lab = Lab(build=True, nodes=(Node(5, good),), artifacts_dir=root)
                try:
                    with lab:
                        assert (
                            lab.request(5, port=10, payload=b"payload-secret")
                            == b"assertion-secret"
                        )
                except AssertionError:
                    pass
                bundle, manifest = manifest_for(root, lab.project)
                assert manifest["exception"] == "AssertionError"
                assert manifest["operation"] == {
                    "name": "request",
                    "address": 5,
                    "port": 10,
                    "timeoutMs": 1500,
                }
                assert "uid 10001" in next(bundle.glob("custom5-*.log")).read_text()
                assert {item["service"] for item in manifest["containers"]} == {
                    "hub",
                    "node2",
                    "node3",
                    "custom5",
                }
                assert not manifest["captureErrors"], manifest
                docker("image", "inspect", good)

                expected = Lab(build=True, nodes=(Node(5, good),), artifacts_dir=root)
                with expected:
                    expected.stop_node(5)
                    try:
                        expected.request(5, port=10, payload=b"caught", timeout_ms=100)
                    except RequestTimeout:
                        pass
                    else:
                        raise AssertionError("Stopped node replied")
                assert not (root / expected.project).exists()
                assert unrelated.ping(3).reachable
        print(
            "Failure artifacts passed: startup/readiness/assertion failures, "
            "200-line tail, retained logs, scoped metadata"
        )
    finally:
        for tag in (good, bad, missing):
            subprocess.run(["docker", "image", "rm", tag], capture_output=True, timeout=120)


if __name__ == "__main__":
    main()
