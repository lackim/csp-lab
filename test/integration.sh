#!/usr/bin/env bash
set -euo pipefail

cleanup() {
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
