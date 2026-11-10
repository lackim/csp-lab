#!/usr/bin/env bash
set -euo pipefail

smoke_dir=$(mktemp -d)
trap 'rm -rf "$smoke_dir"' EXIT

uv build --out-dir "$smoke_dir/dist"
uv venv "$smoke_dir/venv" --python 3.10
uv pip install --python "$smoke_dir/venv/bin/python" "$smoke_dir"/dist/csp_lab-*.whl
mkdir -p "$smoke_dir/bin" "$smoke_dir/work"

cat > "$smoke_dir/bin/docker" <<'SH'
#!/bin/sh
printf '%s|%s|%s\n' "$PWD" "$CSP_LAB_IMAGE" "$*" >> "$CSP_LAB_FAKE_LOG"
case "$1 $2" in
  'network ls') printf '%s\n' network-id; exit 0;;
  'image inspect') printf 'sha256:%064d\n' 1; exit 0;;
  'container ls') exit 0;;
  'container create')
    request_mode=0
    targets=
    collecting=0
    for arg do
      target=$arg
      if [ "$arg" = request ]; then request_mode=1; fi
      # Probe arguments are: probe VERSION ADDRESS TARGET...
      if [ "$collecting" -gt 2 ]; then targets="$targets $arg"; fi
      if [ "$collecting" -gt 0 ]; then collecting=$((collecting + 1)); fi
      if [ "$arg" = probe ]; then collecting=1; fi
    done
    if [ "$request_mode" = 0 ]; then target=$targets; fi
    printf '%s %s\n' "$request_mode" "$target" > "$CSP_LAB_FAKE_LOG.target"
    printf '%064d\n' 1
    exit 0;;
  'container start')
    read -r request_mode target < "$CSP_LAB_FAKE_LOG.target"
    if [ "$request_mode" = 1 ]; then
      read -r payload
      [ "$payload" = 6869 ] || exit 24
      printf '%s\n' '{"status":"ok","reply":"4849"}'
      exit 0
    fi
    for target in $target; do
      case "$target" in
        2|3|5) printf '{"target":%s,"reachable":true,"rttMs":1}\n' "$target";;
      esac
    done
    exit 0;;
esac
for argument do
  if [ "$argument" = "-f" ]; then
    check_next=1
  elif [ "${check_next:-0}" = 1 ]; then
    [ -f "$argument" ] || exit 23
    check_next=0
  fi
  last=$argument
  if [ "$argument" = ps ]; then is_ps=1; fi
done
if [ "${is_ps:-0}" = 1 ]; then
  printf 'hub\nnode2\nnode3\n'
fi
case "$last" in
  2) printf '%s\n' '{"target":2,"reachable":true,"rttMs":1}' ;;
  3) printf '%s\n' '{"target":3,"reachable":true,"rttMs":1}' ;;
esac
SH
chmod +x "$smoke_dir/bin/docker"

export XDG_STATE_HOME="$smoke_dir/state"
export CSP_LAB_FAKE_LOG="$smoke_dir/docker.log"
export PATH="$smoke_dir/bin:$PATH"
cd "$smoke_dir/work"

"$smoke_dir/venv/bin/csp-lab" up --protocol 2 --json
status=$("$smoke_dir/venv/bin/csp-lab" status --json)
[ "$status" = '{"running":true,"state":{"protocol":2,"libcspVersion":"2.1"}}' ]
ping=$("$smoke_dir/venv/bin/csp-lab" ping 2 --json)
[ "$ping" = '{"target":2,"reachable":true,"rttMs":1}' ]
"$smoke_dir/venv/bin/csp-lab" down --json
[ ! -f "$XDG_STATE_HOME/csp-lab/state.json" ]

"$smoke_dir/venv/bin/python" - "$CSP_LAB_FAKE_LOG" "$smoke_dir/work" <<'PY'
from pathlib import Path
from importlib.metadata import version
import sys

calls = Path(sys.argv[1]).read_text().splitlines()
work = sys.argv[2]
assert calls
image = f"ghcr.io/lackim/csp-lab:v{version('csp-lab')}"
assert all(line.startswith(f"{work}|") for line in calls)
assert all(line.startswith(f"{work}|{image}|") for line in calls if "|compose " in line)
assert any(image in line and "|container create " in line for line in calls)
assert any("up -d --pull missing hub node2 node3" in line for line in calls)
assert any("-p csp-lab -f " in line for line in calls)
PY

"$smoke_dir/venv/bin/python" - <<'PY'
from csp_lab import Lab

with Lab(protocol=2) as lab:
    assert lab.ping(2).reachable
    assert lab.topology().nodes == [2, 3]
    assert lab.diagnose().healthy
PY

uv pip install --python "$smoke_dir/venv/bin/python" 'pytest>=8,<10'
cat > "$smoke_dir/work/test_plugin.py" <<'PY'
import pytest
from csp_lab import Node, RequestTimeout

def test_auto_discovered_fixture(csp_lab):
    assert csp_lab.ping(2).reachable

@pytest.mark.csp_lab(nodes=(Node(5, "wheel-app:local"),))
def test_custom_node_and_request_from_installed_wheel(csp_lab):
    assert csp_lab.nodes == (Node(5, "wheel-app:local"),)
    assert csp_lab.topology().nodes == [2, 3, 5]
    assert csp_lab.request(5, port=10, payload=b"hi") == b"HI"
    assert issubclass(RequestTimeout, TimeoutError)
PY
"$smoke_dir/venv/bin/python" -m pytest -q "$smoke_dir/work/test_plugin.py"
