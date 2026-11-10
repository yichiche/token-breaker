# Token Breaker

Every token breaks a brick. Each token a model streams fires a laser at one brick, so the wall falls exactly as fast as
the model writes. It was built as a booth demo for Qwen3.8-27B (Quark INT4) running locally with SGLang on one AMD Ryzen
AI MAX+ 395 (Strix Halo, Radeon 8060S), where visitors watch the speed rather than read the text.

- `index.html`: the game, one file with no dependencies, so it works offline.
- `server.py`: serves the page and streams completions from a backend as server-sent events, one round at a time.
  With SGLang the token count of every chunk is exact (`continuous_usage_stats`), so one brick is one token.
- `setup_container.sh`, `download_models.sh`, `run_sglang.sh`: the container setup, the weights and the local model
  server.

## Run

Everything runs in a container from `rocm/sgl-dev:v0.5.23-rocm724-gfx1151-20261030`, which already has the Python
packages the demo uses (aiohttp, huggingface_hub, Triton 3.7, PyTorch). On Windows 11 with WSL2 (Docker Desktop):

```bash
docker run -it --name token-breaker --network=host --ipc=host --shm-size 16G \
  --device=/dev/dxg -v /usr/lib/wsl/lib/libdxcore.so:/usr/lib/libdxcore.so \
  -v C:/Users/<you>/data:/data -v C:/path/to/token-breaker:/token-breaker \
  rocm/sgl-dev:v0.5.23-rocm724-gfx1151-20261030 bash
```

On native Linux, pass the usual ROCm devices instead of the two WSL flags
(`--device=/dev/kfd --device=/dev/dri --group-add video`); that path was not tested here. Then, inside the container:

```bash
cd /token-breaker
./setup_container.sh                    # WSL GPU libraries, SGLang with sglang#47695 at a pinned commit, GPU check
./download_models.sh                    # Qwen3.8-27B Quark INT4 + DFlash2 drafter, about 22 GB, into /data/models
./run_sglang.sh                         # SGLang on 127.0.0.1:30000; a cold start takes about 6.5 minutes
python3 server.py --backend sglang      # then open http://localhost:8765
```

Behind a TLS-inspecting proxy, add the proxy's CA to the container's system store (`update-ca-certificates`) before the
setup and download steps.

`setup_container.sh` is needed until [sgl-project/sglang#47695](https://github.com/sgl-project/sglang/pull/47695)
(Quark INT4 W4A16 and gfx1151 kernels) merges, because the image's own SGLang cannot load the Quark INT4 checkpoint. It
adds a checkout of the PR beside the image's one (`run_sglang.sh` reads it from `SGLANG_SRC`), installs `rocdxg-roct`
from [ROCm/librocdxg](https://github.com/ROCm/librocdxg/releases) when `/dev/dxg` exists, and checks that PyTorch sees
the GPU. Accuracy on this image was not re-measured; the PR reports GSM8K 0.965 for this checkpoint on the 20261102
image.

The game server alone needs only Python 3.10+ with `aiohttp`. Without a GPU, the same page runs on two test backends,
each labelled on screen:

```bash
python3 server.py --backend mock                           # paced fake tokens
python3 server.py --backend claude --claude-model haiku    # Claude Code CLI: `claude auth login` or ANTHROPIC_API_KEY
```

On the booth page, **Enter** starts, **Esc** cancels, **F** toggles fullscreen.
**T** cycles 1 to 4 requests while you are not typing in the prompt box.
URL options: `?turrets=4`, `?tokens=192` (bricks per request), `?attract=20` (auto-play after N s idle; `?attract=0` turns it off).

### Native Linux booth

From this repo, with ROCm 7.2.4, Docker, and the Quark INT4 plus DFlash2 weights
under `$HOME/data/models`:

```bash
./start_demo.sh
```

Open **http://localhost:8765**. How to drive the page is in [NOTES.md](NOTES.md).
`./stop_demo.sh` deletes the container. `start_demo.sh` creates a new one from
`rocm/sgl-dev:v0.5.23-rocm724-gfx1151-20261109` and launches the installed
SGLang (`SGLANG_SRC=` empty), which already has the gfx1151 kernels.

## Turrets

Each turret is its own request, running at the same time as the others with a different prompt, and clears its own lane
of the wall. The large number becomes total throughput and each turret shows its own speed. In one run on the Radeon 8060S,
one turret decoded at 24 tok/s, while four turrets ran at 8 to 12 tok/s each and 33 tok/s in total: the machine is
at its best serving one person at a time.

## Notes

- Speed depends on what the model writes. Measured single-request decode ranged from about 19 to 35 tok/s across the
  three stages, with about 0.5 to 1 s to the first token. The DFlash2 drafter's guesses are accepted 4 to 6 tokens per
  verify step on tables, code and this translation prompt, against 2.4 to 2.7 on free English prose and about 2 on
  Chinese prose (about 10 tok/s). The stage prompts are chosen for that, and each asks for more than one wall of text,
  because speculative decoding ignores `min_tokens`.
- On load, the page asks the server to run every stage and turret combination once (`/api/warmup`, about 70 s the first
  time, with the buttons locked). Without it, the first round with a new combination waited up to 15 s on Triton
  compiling kernels for the new shapes.
- The browser shares the GPU with the model, so the page draws only while something moves and keeps the wall and the
  hidden message in cached layers.
- Under WSL, ROCm before 7.13 looks for the GPU through `/dev/dxg` only with `HSA_ENABLE_DXG_DETECTION=1`;
  `run_sglang.sh` sets it when `/dev/dxg` exists.
