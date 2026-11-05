"""Run unchanged application assertions against correct and incorrect images."""

import json
import os
import secrets
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from custom_node_lifecycle import containers, docker

from csp_lab import Lab
from csp_lab.docker import cleanup_owned_project


def run_tests(image: str, selector: str, root: Path, plugin: str, expected: int) -> None:
    root.mkdir()
    report = root / "results.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            selector,
            "-p",
            plugin,
            "--junitxml",
            str(report),
            "--csp-lab-artifacts",
            str(root / "artifacts"),
        ],
        env={
            **os.environ,
            "CSP_LAB_EXAMPLE_IMAGE": image,
            "CSP_LAB_EXAMPLE_BUILD": "1",
            "PYTHONPATH": str(root.parent) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    cases = list(ET.parse(report).iter("testcase"))
    assert len(cases) == expected
    assert all(case.find("error") is None and case.find("skipped") is None for case in cases), (
        result.stdout
    )
    if root.name == "correct":
        assert result.returncode == 0, result.stdout + result.stderr
        assert all(case.find("failure") is None for case in cases)
        assert not (root / "artifacts").exists()
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert all(case.find("failure") is not None for case in cases)
        assert all("AssertionError" in (case.find("failure").text or "") for case in cases)
        manifests = list((root / "artifacts").rglob("manifest.json"))
        assert len(manifests) == expected
        for path in manifests:
            manifest = json.loads(path.read_text())
            assert manifest["exception"] == "AssertionError"
            assert manifest["operation"] == {
                "name": "request",
                "address": 5,
                "port": 10,
                "timeoutMs": 1500,
            }
            assert "hello:" not in path.read_text()
            assert containers(manifest["project"]) == ""
            assert next(path.parent.glob("custom5-*.log")).is_file()
            assert any(
                item["service"] == "custom5" and item["status"] == "running"
                for item in manifest["containers"]
            )


def main() -> None:
    token = secrets.token_hex(8)
    correct = f"csp-lab-uppercase:pytest-{token}"
    incorrect = f"csp-lab-uppercase:pytest-broken-{token}"
    try:
        for image, flag in ((correct, "OFF"), (incorrect, "ON")):
            docker(
                "build",
                "-f",
                "examples/custom-node/Dockerfile",
                "--build-arg",
                f"INCORRECT={flag}",
                "-t",
                image,
                ".",
            )
        identities = {
            image: docker("image", "inspect", "--format", "{{.Id}}", image)
            for image in (correct, incorrect)
        }
        with tempfile.TemporaryDirectory(prefix="csp-lab-pytest-proof-") as directory:
            root = Path(directory)
            ownership = root / "projects.txt"
            plugin = f"csp_lab_record_ownership_{token}"
            # Test-only bookkeeping records each project before startup, so a
            # failed/timed-out pytest worker can always be recovered by its owner.
            (root / f"{plugin}.py").write_text(
                "from csp_lab import Lab\n"
                "original_enter = Lab.__enter__\n"
                "def record_enter(lab):\n"
                f"    with open({str(ownership)!r}, 'a') as output:\n"
                "        output.write(lab.project + '\\n')\n"
                "    return original_enter(lab)\n"
                "Lab.__enter__ = record_enter\n"
            )
            try:
                with Lab(build=True, artifacts_dir=None) as unrelated:
                    selector = "examples/custom-node/tests/test_application.py"
                    run_tests(correct, selector, root / "correct", plugin, 6)
                    assert unrelated.ping(2).reachable
                    run_tests(
                        incorrect, selector + "::test_uppercase", root / "incorrect", plugin, 2
                    )
                    assert unrelated.ping(3).reachable
                    projects = ownership.read_text().splitlines()
                    assert len(projects) == len(set(projects)) == 6
                    assert all(containers(project) == "" for project in projects)
                    for image, identity in identities.items():
                        assert docker("image", "inspect", "--format", "{{.Id}}", image) == identity
            finally:
                if ownership.exists():
                    errors = []
                    for project in dict.fromkeys(ownership.read_text().splitlines()):
                        try:
                            cleanup_owned_project(project)
                        except Exception as exc:
                            errors.append(project)
                            print(
                                f"Recovery failed: csp-lab cleanup {project}: {exc}",
                                file=sys.stderr,
                            )
                    if errors and sys.exc_info()[1] is None:
                        raise RuntimeError(f"Project recovery incomplete: {errors}")
        print(
            "Custom pytest passed: unchanged assertions catch broken apps in CSP v1/v2; "
            "restart, artifacts, isolation"
        )
    finally:
        for image in (correct, incorrect):
            subprocess.run(["docker", "image", "rm", image], capture_output=True, timeout=120)


if __name__ == "__main__":
    main()
