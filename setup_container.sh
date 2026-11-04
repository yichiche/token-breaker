#!/usr/bin/env bash
# One-time setup inside a rocm/sgl-dev:v0.5.23-rocm724-gfx1151-20261030 container; safe to run again.
set -euo pipefail
PR_COMMIT=54ab633be592d11146c101e5b49a2071b9c315f3
PR_DIR=${SGLANG_SRC:-/sgl-workspace/sglang-pr47695}
ROCDXG_DEB=https://github.com/ROCm/librocdxg/releases/download/v1.2.2/rocdxg-roct_1.2.2_amd64.deb

# Under WSL the GPU is /dev/dxg, which ROCm reaches through librocdxg and Microsoft's libdxcore.
if [ -e /dev/dxg ]; then
  if ! dpkg -s rocdxg-roct >/dev/null 2>&1; then
    curl -fsSL -o /tmp/rocdxg-roct.deb "$ROCDXG_DEB"
    dpkg -i /tmp/rocdxg-roct.deb
  fi
  ln -sf /opt/rocm/lib/librocdxg.so /usr/lib/librocdxg.so
  mkdir -p /usr/share/rocdxg
  ln -sf /opt/rocm/share/rocdxg/dids.conf /usr/share/rocdxg/dids.conf
  if [ ! -e /usr/lib/libdxcore.so ]; then
    echo "missing /usr/lib/libdxcore.so; start the container with" \
      "-v /usr/lib/wsl/lib/libdxcore.so:/usr/lib/libdxcore.so" >&2
    exit 1
  fi
  ldconfig
  export HSA_ENABLE_DXG_DETECTION=1
fi

# sgl-project/sglang#47695 beside the image's own checkout, which carries a stale GitHub auth header from CI that
# makes even public fetches fail.
if [ ! -d "$PR_DIR/python/sglang" ]; then
  git -C /sgl-workspace/sglang -c http.https://github.com/.extraheader= -c credential.helper= \
    -c credential.https://github.com.helper= fetch --quiet https://github.com/hubertlu-tw/sglang "$PR_COMMIT"
  git -C /sgl-workspace/sglang worktree add --detach "$PR_DIR" "$PR_COMMIT"
fi
echo "sglang with #47695: $(git -C "$PR_DIR" rev-parse --short HEAD) at $PR_DIR"

python3 -c "import torch; assert torch.cuda.is_available(), 'no GPU visible'; print('GPU:', torch.cuda.get_device_name(0))"
