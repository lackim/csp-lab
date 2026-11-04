"""Real Docker interruption/recovery checks, using only isolated Lab projects."""

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from csp_lab import Lab, docker

Run = Callable[[str, list[str], dict[str, str] | None, float], str]


def stalled_probe(
    original: Run,
    ready: Path | None = None,
    interrupt: bool = False,
    lost_ack: bool = False,
) -> Run:
    def execute(
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        timeout: float = 120,
    ) -> str:
        if args[:2] == ["container", "create"]:
            image_index = args.index("--network") + 2
            args = [
                *args[:image_index],
                "--entrypoint",
                "/bin/sh",
                args[image_index],
                "-c",
                "sleep 30; echo unexpected-late-send",
            ]
            output = original(command, args, env, timeout)
            if ready is not None:
                ready.write_text(output.strip())
            if lost_ack:
                raise subprocess.TimeoutExpired("docker create", timeout)
            return output
        if args[:2] == ["container", "start"] and ready is None:
            if interrupt:
                timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGINT))
                timer.start()
                try:
                    return original(command, args, env, timeout)
                finally:
                    timer.cancel()
                    timer.join()
            return original(command, args, env, 0.5)
        return original(command, args, env, timeout)

    return execute


def worker(project: str, image: str, ready: Path) -> None:
    with patch.object(docker, "run", stalled_probe(docker.run, ready)):
        docker.ProbeOwner(project).execute(["probe", "2", "4", "2"], image)


def probe_ids(project: str) -> list[str]:
    return docker.run(
        "docker",
        [
            "container",
            "ls",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.service=probe",
        ],
    ).splitlines()


def main() -> None:
    with Lab(protocol=2, build=True) as unrelated, Lab(protocol=2, build=True) as lab:
        with patch.object(docker, "run", stalled_probe(docker.run, lost_ack=True)):
            try:
                lab.ping(2)
            except RuntimeError as exc:
                assert "exceeded its deadline" in str(exc)
            else:
                raise AssertionError("Expected lost create acknowledgement")
        assert not probe_ids(lab.project)
        assert lab.ping(2).reachable
        for interrupt in (False, True):
            with patch.object(docker, "run", stalled_probe(docker.run, interrupt=interrupt)):
                try:
                    lab.ping(2)
                except KeyboardInterrupt:
                    assert interrupt
                except RuntimeError as exc:
                    assert not interrupt and "exceeded its deadline" in str(exc)
                else:
                    raise AssertionError("Expected timeout/cancellation")
            assert not probe_ids(lab.project)
            assert lab.ping(2).reachable
        with TemporaryDirectory(prefix="csp-probe-recovery-") as directory:
            ready = Path(directory) / "container"
            process = subprocess.Popen(
                [sys.executable, __file__, "--worker", lab.project, lab.image, str(ready)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, "worker exited before its probe was running"
                    if ready.exists():
                        running = docker.run(
                            "docker",
                            [
                                "container",
                                "inspect",
                                "--format",
                                "{{.State.Running}}",
                                ready.read_text(),
                            ],
                            timeout=5,
                        ).strip()
                        if running == "true":
                            break
                    assert time.monotonic() < deadline, "worker did not start a probe"
                    time.sleep(0.05)
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                assert probe_ids(lab.project), "expected a daemon-owned orphan after SIGKILL"
                docker.cleanup_owned_project(lab.project)
                assert not probe_ids(lab.project)
                assert unrelated.ping(2).reachable
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        print("Probe timeout, SIGINT, killed-worker recovery, and unrelated-lab isolation passed")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        worker(sys.argv[2], sys.argv[3], Path(sys.argv[4]))
    else:
        main()
