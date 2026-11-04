#!/usr/bin/env bash
set -euo pipefail

image="csp-lab-bounds:$$-$RANDOM"
container="csp-lab-bounds-$$-$RANDOM"
cleanup() {
  result=$?
  trap - EXIT
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
  exit "$result"
}
trap cleanup EXIT

docker build --target native-tests -t "$image" .
docker run --rm --name "$container" --add-host hub:127.0.0.1 "$image"
