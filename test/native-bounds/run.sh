#!/usr/bin/env bash
set -euo pipefail

logs=$(mktemp -d)
hub_pid=
node_pid=
cleanup() {
  result=$?
  trap - EXIT
  for pid in "$node_pid" "$hub_pid"; do
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
  done
  if [ "$result" != 0 ]; then cat "$logs"/* >&2; fi
  rm -rf "$logs"
  exit "$result"
}
trap cleanup EXIT

cd /app/build
./csp-lab-frame-tests
for protocol in 1 2; do
  ./vendor/libcsp/examples/zmqproxy -v "$protocol" > "$logs/hub-$protocol" 2>&1 &
  hub_pid=$!
  ./csp-lab-native server "$protocol" 2 127.0.0.1 > "$logs/node-$protocol" 2>&1 &
  node_pid=$!
  ready=0
  for attempt in 1 2 3 4 5; do
    if ./csp-lab-native probe "$protocol" 4 2 > "$logs/ready-$protocol" && \
       grep -q '"reachable":true' "$logs/ready-$protocol"; then
      ready=1
      break
    fi
  done
  [ "$ready" = 1 ]
  ./csp-lab-frame-injector "$protocol"
  kill -0 "$hub_pid" "$node_pid"
  # The stock probe expects DNS name "hub"; the test container maps it to localhost.
  ./csp-lab-native probe "$protocol" 4 2 | tee "$logs/probe-$protocol" | grep '"reachable":true'
  kill "$node_pid" "$hub_pid"
  wait "$node_pid" 2>/dev/null || true
  wait "$hub_pid" 2>/dev/null || true
  node_pid=
  hub_pid=
done
if grep -E 'ERROR: AddressSanitizer|AddressSanitizer:DEADLYSIGNAL' "$logs"/*; then
  exit 1
fi
