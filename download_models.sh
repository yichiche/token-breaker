#!/usr/bin/env bash
# Booth model and its DFlash2 drafter, as plain files under /data so they survive container re-creation
# and the server can start offline.
set -euo pipefail
# A TLS-inspecting proxy's CA lives in the system bundle, which Python's certifi bundle does not include.
export SSL_CERT_FILE=${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}
export REQUESTS_CA_BUNDLE=${REQUESTS_CA_BUNDLE:-$SSL_CERT_FILE}
DEST=${DEST:-/data/models}
for repo in amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16 incoai/Qwen3.8-27B-DFlash2; do
  dir="$DEST/${repo#*/}"
  if [ -f "$dir/.complete" ]; then
    echo "have $dir"
    continue
  fi
  hf download "$repo" --local-dir "$dir"
  touch "$dir/.complete"
  echo "done $dir"
done
