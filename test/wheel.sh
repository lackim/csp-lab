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
assert all(line.startswith(f"{work}|{image}|") for line in calls)
assert any("up -d --pull always hub node2 node3" in line for line in calls)
assert any("-p csp-lab -f " in line for line in calls)
PY
