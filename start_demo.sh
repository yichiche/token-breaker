#!/usr/bin/env bash
# Create a fresh container from the gfx1151 SGLang image and start the booth.
# No container has to exist beforehand. Models are read from $DATA/models on the host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA="${DATA:-$HOME/data}"
IMAGE="${IMAGE:-rocm/sgl-dev:v0.5.23-rocm724-gfx1151-20261109}"
NAME="${NAME:-token-breaker}"
PIN_CPUS="${PIN_CPUS:-0-3}"
SGLANG_PORT="${SGLANG_PORT:-30000}"
UI_PORT="${UI_PORT:-8765}"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

need() {
  local p="$1"
  if [[ ! -e "$p" ]]; then
    echo "missing: $p" >&2
    exit 1
  fi
}

need "$ROOT/run_sglang.sh"
need "$ROOT/server.py"
need "$DATA/models/Qwen3.8-27B-Quark-AWQ-INT4-W4A16"
need "$DATA/models/Qwen3.8-27B-DFlash2"
need /dev/kfd
need /dev/dri/renderD128

if [[ ! -e /opt/rocm/.info/version ]]; then
  echo "ROCm not found under /opt/rocm — install 7.2.4 first (see NOTES.md)" >&2
  exit 1
fi

log "ROCm $(cat /opt/rocm/.info/version)"

if docker info >/dev/null 2>&1; then
  docker() { command docker "$@"; }
elif sudo -n docker info >/dev/null 2>&1; then
  docker() { command sudo -n docker "$@"; }
else
  echo "docker is not usable. Join the docker group, or allow passwordless sudo for docker." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  sudo -n systemctl start docker
fi

VIDEO_GID="$(getent group video | cut -d: -f3)"
RENDER_GID="$(getent group render | cut -d: -f3)"

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  log "Removing previous container $NAME"
  docker rm -f "$NAME" >/dev/null
fi

log "Creating container $NAME from $IMAGE"
docker run -d --name "$NAME" --network=host --ipc=host --shm-size 16G \
  --device=/dev/kfd --device=/dev/dri \
  --group-add "$VIDEO_GID" \
  --group-add "$RENDER_GID" \
  --security-opt seccomp=unconfined \
  -v "$DATA:/data" \
  -v "$ROOT:/token-breaker" \
  "$IMAGE" \
  sleep infinity >/dev/null

log "Checking GPU inside container"
docker exec "$NAME" python3 -c 'import torch; assert torch.cuda.is_available(), "no GPU"; print("GPU:", torch.cuda.get_device_name(0))'

log "Starting SGLang (cold start can take several minutes) → $DATA/sglang.log"
: > "$DATA/sglang.log" 2>/dev/null || sudo -n truncate -s 0 "$DATA/sglang.log" || true
docker exec -d "$NAME" bash -lc \
  "cd /token-breaker && SGLANG_SRC= PIN_CPUS=${PIN_CPUS} ./run_sglang.sh >> /data/sglang.log 2>&1"

log "Waiting for http://127.0.0.1:${SGLANG_PORT}/health"
for i in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:${SGLANG_PORT}/health" >/dev/null 2>&1; then
    log "SGLang healthy after ~$((i * 5))s"
    break
  fi
  if (( i % 6 == 0 )); then
    log "still waiting… (tail sglang.log)"
    tail -n 3 "$DATA/sglang.log" 2>/dev/null || true
  fi
  if (( i == 180 )); then
    echo "SGLang did not become healthy — see $DATA/sglang.log" >&2
    exit 1
  fi
  sleep 5
done

docker exec "$NAME" bash -lc "
  for p in \$(ps -eo pid,args | awk '/sglang/ && !/awk/ && !/ps -eo/ {print \$1}'); do
    taskset -a -p -c ${PIN_CPUS} \"\$p\" >/dev/null 2>&1 || true
  done
" || true

log "Starting Token Breaker UI → $DATA/token-breaker-ui.log"
docker exec -d "$NAME" bash -lc \
  "cd /token-breaker && python3 server.py --backend sglang --host 0.0.0.0 --port ${UI_PORT} >> /data/token-breaker-ui.log 2>&1"
sleep 2

if curl -sf "http://127.0.0.1:${UI_PORT}/" >/dev/null 2>&1; then
  log "Ready."
  log "  SGLang:  http://127.0.0.1:${SGLANG_PORT}/health"
  log "  Demo UI: http://localhost:${UI_PORT}"
  log "  Logs:    tail -f $DATA/sglang.log"
else
  echo "UI failed to start — see $DATA/token-breaker-ui.log" >&2
  exit 1
fi
