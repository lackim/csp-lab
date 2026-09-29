#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  node dist/cli.js down >/dev/null 2>&1 || true
}
trap cleanup EXIT

for protocol in 1 2; do
  node dist/cli.js up --protocol "$protocol"
  node dist/cli.js status --json
  node dist/cli.js doctor --json
  npm run test:mcp
  node dist/cli.js down
done
