#!/usr/bin/env bash
# Stop the booth and delete its container. The image and $DATA/models stay on disk.
set -euo pipefail

NAME="${NAME:-token-breaker}"

if docker info >/dev/null 2>&1; then
  docker() { command docker "$@"; }
elif sudo -n docker info >/dev/null 2>&1; then
  docker() { command sudo -n docker "$@"; }
else
  echo "docker is not usable. Join the docker group, or allow passwordless sudo for docker." >&2
  exit 1
fi

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "Removing container $NAME"
  docker rm -f "$NAME" >/dev/null
else
  echo "container $NAME is not present"
fi
