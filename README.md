# Token Breaker

Every token breaks a brick. Each token a model streams fires a laser at one brick, so the wall falls exactly as fast as
the model writes. It was built as a booth demo for Qwen3.8-27B (Quark INT4) running locally with SGLang on one AMD Ryzen
AI MAX+ 395 (Strix Halo, Radeon 8060S), where visitors watch the speed rather than read the text.

- `index.html`: the game, one file with no dependencies, so it works offline.
- `server.py`: serves the page and streams completions from a backend as server-sent events, one round at a time.
  With SGLang the token count of every chunk is exact (`continuous_usage_stats`), so one brick is one token.
- `run_sglang.sh`, `download_models.sh`: the local model server and its weights.

## Run

The game server needs Python 3.10+ with `aiohttp`. The local model needs SGLang with
[sgl-project/sglang#47695](https://github.com/sgl-project/sglang/pull/47695) (Quark INT4 W4A16 and gfx1151 kernels)
until it merges; `run_sglang.sh` uses a checkout of it from `SGLANG_SRC`.

```bash
./download_models.sh                    # Qwen3.8-27B Quark INT4 + DFlash2 drafter, about 22 GB, into /data/models
./run_sglang.sh                         # SGLang on 127.0.0.1:30000; a cold start takes about 6.5 minutes
python3 server.py --backend sglang      # then open http://localhost:8765
```

Without a GPU, the same page runs on two test backends, each labelled on screen:

```bash
python3 server.py --backend mock                           # paced fake tokens
python3 server.py --backend claude --claude-model haiku    # Claude Code CLI: `claude auth login` or ANTHROPIC_API_KEY
```

Keys: `1` `2` `3` start a stage, `T` cycles 1 to 4 turrets, `Esc` cancels, `F` toggles fullscreen.
URL options: `?turrets=4`, `?tokens=192` (bricks per turret), `?attract=45` (play by itself after 45 s idle).

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
- Windows 11 with WSL2 (Docker Desktop): the GPU is reached through `/dev/dxg`. In the container, install `rocdxg-roct`
  from [ROCm/librocdxg](https://github.com/ROCm/librocdxg/releases), copy `libdxcore.so` from `/usr/lib/wsl/lib` of the
  `docker-desktop` distro to `/usr/lib`, and set `HSA_ENABLE_DXG_DETECTION=1` with ROCm before 7.13 (`run_sglang.sh`
  does when `/dev/dxg` exists).
