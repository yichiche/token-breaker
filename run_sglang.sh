#!/usr/bin/env bash
# Local SGLang server for the booth: Qwen3.8-27B Quark INT4 with the DFlash2 drafter on one Strix Halo.
# Needs sgl-project/sglang#47695 (Quark int4 W4A16 + gfx1151 kernels) until it merges; flags follow that PR.
set -euo pipefail
MODELS=${MODELS:-/data/models}
PORT=${PORT:-30000}
PIN_CPUS=${PIN_CPUS-0-3}
# A checkout of the PR; set SGLANG_SRC= to use the installed sglang once it merges.
SGLANG_SRC=${SGLANG_SRC-/sgl-workspace/sglang-pr47695}

if [ -n "$SGLANG_SRC" ]; then
  if [ ! -d "$SGLANG_SRC/python/sglang" ]; then
    echo "no sglang checkout at $SGLANG_SRC" >&2
    exit 1
  fi
  export PYTHONPATH="$SGLANG_SRC/python${PYTHONPATH:+:$PYTHONPATH}"
fi
# Under WSL the GPU is reached through /dev/dxg and librocdxg; ROCm before 7.13 only looks there when asked.
if [ -e /dev/dxg ]; then
  export HSA_ENABLE_DXG_DETECTION=1
fi
export SGLANG_MAMBA_SSM_DTYPE=bfloat16
python3 -m sglang.launch_server \
  --model-path "$MODELS/Qwen3.8-27B-Quark-AWQ-INT4-W4A16" \
  --served-model-name qwen38-awq \
  --attention-backend triton \
  --host 127.0.0.1 --port "$PORT" \
  --mem-fraction-static 0.85 \
  --max-running-requests 4 \
  --chunked-prefill-size 4096 \
  --max-total-tokens 131072 \
  --reasoning-parser qwen3 \
  --speculative-algorithm DFLASH \
  --speculative-draft-model-path "$MODELS/Qwen3.8-27B-DFlash2" \
  --speculative-draft-attention-backend triton \
  --speculative-num-draft-tokens 8 &
server=$!
trap 'kill "$server" 2>/dev/null' INT TERM

until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; do
  if ! kill -0 "$server" 2>/dev/null; then
    wait "$server"
    exit 1
  fi
  sleep 5
done

# HIP's busy-waiting host threads otherwise take power from the GPU on this APU.
if [ -n "$PIN_CPUS" ]; then
  for pid in $(pgrep -f 'sglang\.launch_server|sglang::'); do
    taskset -a -p -c "$PIN_CPUS" "$pid" >/dev/null
  done
  echo "pinned SGLang to CPUs $PIN_CPUS"
fi
echo "SGLang ready on http://127.0.0.1:$PORT"
wait "$server"
