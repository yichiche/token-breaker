# Drive the Strix Halo demo

Clone this repo and run it on a Strix Halo machine. No container has to exist
beforehand. Weights stay on the host under `$DATA/models` (default `$HOME/data/models`).

**Target:** AMD Ryzen AI MAX+ 395 · Radeon 8060S · gfx1151  
**Model:** Qwen3.8-27B Quark INT4, served by SGLang with DFlash2 (local, no cloud)  
**Image:** `rocm/sgl-dev:v0.5.23-rocm724-gfx1151-20261109`  
**UI:** http://localhost:8765

The image already includes the gfx1151 kernels from
[sglang#47695](https://github.com/sgl-project/sglang/pull/47695), so `start_demo.sh`
launches SGLang with `SGLANG_SRC=` empty.

## Before the first start

Host packages: ROCm 7.2.4 userspace (`rocminfo` shows gfx1151), Docker, and
`/dev/kfd` plus `/dev/dri/renderD128`. The user needs to run `docker` directly,
or passwordless `sudo docker`.

Models, about 22 GB:

```bash
mkdir -p "$HOME/data/models"
# huggingface_hub CLI, from a venv if the system Python blocks pip
hf download amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16 \
  --local-dir "$HOME/data/models/Qwen3.8-27B-Quark-AWQ-INT4-W4A16"
hf download incoai/Qwen3.8-27B-DFlash2 \
  --local-dir "$HOME/data/models/Qwen3.8-27B-DFlash2"
```

Pull the image once: `docker pull rocm/sgl-dev:v0.5.23-rocm724-gfx1151-20261109`.

## Start

```bash
cd /path/to/token-breaker
./start_demo.sh
```

`DATA=/some/other/data ./start_demo.sh` if the weights are not in `$HOME/data`.

The script creates a new `token-breaker` container from the image, mounts this
repo at `/token-breaker` and `$DATA` at `/data`, then starts SGLang on port
**30000** and the demo on port **8765**. A cold start often takes several
minutes while weights load. Leave the terminal open until it prints `Ready.`

Then open **http://localhost:8765**. Press **F** for fullscreen.

## Wait until it says Ready

The yellow banner counts kernel warmup (`Warming kernels 3/14`). The first
visitor request is slow until that finishes. When the banner says
**Ready · press Start**, the booth is live.

```bash
curl -s http://127.0.0.1:30000/health          # SGLang: empty 200 when up
curl -s http://127.0.0.1:8765/api/health       # "warming": false when ready
```

## What the screen is

Each token the model writes knocks down one brick. The wall falls as fast as the
model writes. The big number on the right is decode speed in **tok/s**. It does
not include time to first token.

The brick total is the token budget, chosen before the model speaks:

| Choice | Bricks |
| --- | --- |
| One request | 192 |
| LeetCode problem | 384 |
| Requests set to N | budget × N |

A short answer stops early. **2 / 192** means the model wrote 2 tokens and the
wall was built for 192. Leftover bricks clear when the answer ends. **Time to
first token** is how long the first brick waited.

## Drive a round

1. Type a question in the box, or click a topic. The topic fills the box so you
   can read it and edit it.
2. Press the green **Start** button, or press **Enter**. Clicking the wall also
   starts.
3. **Requests** 1–4 runs that many questions at once. Each one gets its own
   slice of the wall.
4. When the round finishes, click **View output** to read the full answer and
   the question that produced it. With more than one request, use the tabs.
   **Close**, Esc, or a click outside the panel dismisses it.

Topics:

| Button | What it asks |
| --- | --- |
| Months | A 12-month table: days, season in Taiwan, festival |
| To-do list | A small web page that can add, delete, and check off tasks |
| Translate | English translation of the booth sentence, then an explanation |
| Two Sum, Valid Parentheses, Climbing Stairs | A Python function for that problem |

**Enter** starts. **Shift+Enter** adds a line in the box. **Esc** cancels a
round, or closes View output if that panel is open. **F** toggles fullscreen.
**T** cycles Requests only while you are not typing in the box.

If the box is left empty and nobody touches the page for about 20 seconds, the
demo starts a topic by itself. Typing anything pauses that. To turn it off, open
http://localhost:8765/?attract=0

## Slide backup

[token-breaker-booth.mp4](token-breaker-booth.mp4) is an 18-second, 1920×1080
recording of one Months round: the idle wall, the prompt, bricks falling, the
done card, and View output. Drop it into a slide deck when the live machine is
not available. The table stops at August because the 192-token wall runs out.

## Logs

```bash
tail -f "$HOME/data/sglang.log"
tail -f "$HOME/data/token-breaker-ui.log"
```

## Stop

```bash
./stop_demo.sh
```

That deletes the container. The image and the model files stay on disk. The
next `./start_demo.sh` creates a new container.

## If it will not come up

| What you see | What to do |
| --- | --- |
| SGLang did not become healthy | `tail -n 80 "$HOME/data/sglang.log"` |
| UI banner says the backend is offline | Wait. SGLang is still loading. Then refresh the page. |
| Banner stays on warmup for a couple of minutes | Leave it. The first requests compile kernels. |
| Page looks like an older demo (Stage 1 / 2 / 3) | Refresh. The page is served from the bind mount. |
| `docker` permission denied | Run as a user in the `docker` group, or allow passwordless `sudo docker`. |
