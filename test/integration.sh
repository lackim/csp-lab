#!/usr/bin/env bash
set -euo pipefail

recovery_file=$(mktemp)

cleanup() {
  if [ -s "$recovery_file" ]; then
    uv run --locked csp-lab cleanup "$(cat "$recovery_file")" >/dev/null 2>&1 || true
  fi
  rm -f "$recovery_file"
  uv run --locked csp-lab down >/dev/null 2>&1 || true
}
trap cleanup EXIT

for protocol in 1 2; do
  uv run --locked csp-lab up --protocol "$protocol" --build
  uv run --locked csp-lab status --json
  uv run --locked csp-lab doctor --json
  uv run --locked python tests/mcp_smoke.py
  uv run --locked csp-lab down
done

uv run --locked csp-lab up --protocol 2 --build
uv run --locked python - <<'PY'
import subprocess

from csp_lab import Lab

for protocol in (1, 2):
    with Lab(protocol=protocol, build=True) as lab:
        project = lab.project
        assert lab.ping(2).reachable
        assert lab.topology().protocol == protocol
        assert lab.diagnose().healthy
    assert subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip() == ""
    assert subprocess.run(
        ["docker", "image", "inspect", f"csp-lab-native:{project}"],
        capture_output=True, check=False,
    ).returncode != 0
PY
uv run --locked csp-lab status --json
uv run --locked csp-lab down

RECOVERY_FILE="$recovery_file" uv run --locked python - <<'PY'
import os
from pathlib import Path

from csp_lab import Lab

lab = Lab(protocol=2, build=True)
lab.__enter__()
Path(os.environ["RECOVERY_FILE"]).write_text(lab.project)
os._exit(0)
PY
recovery_project=$(cat "$recovery_file")
[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$recovery_project")" ]
uv run --locked csp-lab cleanup "$recovery_project"
[ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$recovery_project")" ]
! docker image inspect "csp-lab-native:$recovery_project" >/dev/null 2>&1
: > "$recovery_file"
