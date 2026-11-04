#!/usr/bin/env python3
"""Token Breaker: every generated token breaks one brick.

Serves index.html and streams one completion at a time from a backend,
normalized to server-sent events.

    python3 server.py --backend mock
    python3 server.py --backend claude --claude-model haiku
    python3 server.py --backend sglang --sglang-url http://127.0.0.1:30000
"""

import argparse
import asyncio
import contextlib
import json
import os
import random
import re
import shutil
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

ROOT = Path(__file__).resolve().parent
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")
MOCK_TEXT = (
    "This is mock data, not model output; it only exercises the pacing. "
    "Mock data paces tokens like speculative decoding: a few tokens per verify step. "
)
MOCK_PIECES = re.findall(r"[\u4e00-\u9fff\uff00-\uffef\u3000-\u303f]|\s*\w+|\s*[^\w\s]", MOCK_TEXT)


def estimate_tokens(text):
    if not text:
        return 0
    cjk = len(CJK.findall(text))
    return max(1, round(cjk + (len(text) - cjk) / 4))


class MockBackend:
    name = "mock"

    def __init__(self, rate, ttft):
        self.rate = rate
        self.ttft = ttft

    async def health(self):
        return True, "mock"

    async def stream(self, prompt, max_tokens):
        await asyncio.sleep(self.ttft)
        accepted = (1, 2, 3, 4, 4, 5, 5, 6, 7, 7)
        step = sum(accepted) / len(accepted) / self.rate
        sent = 0
        while sent < max_tokens:
            n = min(max_tokens - sent, random.choice(accepted))
            await asyncio.sleep(step * random.uniform(0.9, 1.1))
            text = "".join(MOCK_PIECES[(sent + i) % len(MOCK_PIECES)] for i in range(n))
            yield {"text": text, "tokens": n}
            sent += n
        yield {"usage": {"prompt_tokens": estimate_tokens(prompt), "completion_tokens": sent}, "finish_reason": "length"}


class ClaudeBackend:
    """Claude through the Claude Code CLI, so either its login or ANTHROPIC_API_KEY works."""

    name = "claude"

    def __init__(self, model, binary):
        self.model = model
        self.binary = binary

    async def health(self):
        if shutil.which(self.binary):
            return True, "claude CLI"
        return False, f"{self.binary} not found"

    async def stream(self, prompt, max_tokens):
        env = dict(os.environ, CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(max_tokens), MAX_THINKING_TOKENS="0")
        proc = await asyncio.create_subprocess_exec(
            self.binary, "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--no-session-persistence", "--strict-mcp-config", "--tools", "",
            "--model", self.model,
            "--system-prompt", "You are a helpful assistant.",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=1 << 24,
        )
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        usage, finish, error, streamed = {}, None, None, False
        try:
            async for raw in proc.stdout:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "result":
                    if msg.get("is_error"):
                        error = msg.get("result") or msg.get("subtype")
                    break
                if msg.get("type") != "stream_event":
                    continue
                ev = msg.get("event") or {}
                kind = ev.get("type")
                if kind == "message_start":
                    usage["prompt_tokens"] = (ev.get("message") or {}).get("usage", {}).get("input_tokens")
                elif kind == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    streamed = True
                    yield {"text": ev["delta"]["text"], "tokens": None}
                elif kind == "message_delta":
                    usage["completion_tokens"] = (ev.get("usage") or {}).get("output_tokens")
                    finish = (ev.get("delta") or {}).get("stop_reason")
                elif kind == "message_stop":
                    break
            if error is None and not streamed:
                await proc.wait()
                error = (await proc.stderr.read()).decode(errors="replace").strip()[-400:] or f"claude exited {proc.returncode}"
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        if error:
            raise RuntimeError(error)
        yield {"usage": usage, "finish_reason": finish}


class SGLangBackend:
    name = "sglang"

    def __init__(self, url, model, temperature):
        self.url = url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.session = None

    def _session(self):
        if self.session is None:
            self.session = ClientSession(timeout=ClientTimeout(total=None, sock_connect=5, sock_read=300))
        return self.session

    async def health(self):
        try:
            async with self._session().get(self.url + "/health", timeout=ClientTimeout(total=3)) as r:
                return r.status == 200, f"{self.url} HTTP {r.status}"
        except Exception as e:
            return False, f"{self.url} {type(e).__name__}"

    async def stream(self, prompt, max_tokens):
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            # Keeps the wall clearing on plain decoding. DFlash speculative decoding ignores it, so the stage
            # prompts must also ask for answers longer than max_tokens.
            "min_tokens": max_tokens,
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": True, "continuous_usage_stats": True},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        usage, finish, counted = {}, None, 0
        async with self._session().post(self.url + "/v1/chat/completions", json=payload) as r:
            if r.status != 200:
                raise RuntimeError(f"SGLang HTTP {r.status}: {(await r.text())[:300]}")
            async for raw in r.content:
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                chunk = json.loads(data)
                usage = chunk.get("usage") or usage
                if not chunk.get("choices"):
                    continue
                text = ""
                for choice in chunk["choices"]:
                    delta = choice.get("delta") or {}
                    text += (delta.get("reasoning_content") or "") + (delta.get("content") or "")
                    finish = choice.get("finish_reason") or finish
                n = None
                if chunk.get("usage"):
                    n = chunk["usage"]["completion_tokens"] - counted
                    counted += n
                if text or n:
                    yield {"text": text, "tokens": n}
        yield {"usage": usage, "finish_reason": finish}


async def emit(resp, **payload):
    await resp.write(b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\n\n")


async def generate(backend, prompt, max_tokens, on_token=None, t0=None):
    """Run one completion; decode tok/s excludes TTFT and the first chunk."""
    t0 = time.perf_counter() if t0 is None else t0
    first = last = None
    first_n = streamed = steps = 0
    estimated = False
    usage, finish = {}, None
    async with contextlib.aclosing(backend.stream(prompt, max_tokens)) as events:
        async for item in events:
            if "usage" in item:
                usage, finish = item["usage"] or {}, item.get("finish_reason")
                continue
            n = item["tokens"]
            if n is None:
                n, estimated = estimate_tokens(item["text"]), True
            now = time.perf_counter()
            if n:
                if first is None:
                    first, first_n = now, n
                last = now
                steps += 1
            streamed += n
            if on_token:
                await on_token(item["text"], n, streamed, now - t0)
    total = usage.get("completion_tokens") or streamed
    decode = (total - first_n) / (last - first) if first is not None and last > first else None
    return {
        "completion_tokens": total,
        "streamed_tokens": streamed,
        "chunks": steps,
        "prompt_tokens": usage.get("prompt_tokens"),
        "ttft_s": round(first - t0, 3) if first is not None else None,
        "last_s": round(last - t0, 3) if last is not None else None,
        "first_tokens": first_n,
        "decode_tps": round(decode, 2) if decode else None,
        "e2e_s": round(time.perf_counter() - t0, 3),
        "finish_reason": finish,
        "estimated": estimated,
    }


def combine(results, t0):
    """Totals for requests run side by side; decode tok/s spans the first token of any to the last of any."""
    started = [r for r in results if r["ttft_s"] is not None]
    span = max(r["last_s"] for r in started) - min(r["ttft_s"] for r in started) if started else 0
    decoded = sum(r["completion_tokens"] - r["first_tokens"] for r in started)
    reasons = {r["finish_reason"] for r in results}
    return {
        "lanes": results,
        "completion_tokens": sum(r["completion_tokens"] for r in results),
        "chunks": sum(r["chunks"] for r in results),
        "prompt_tokens": sum(r["prompt_tokens"] or 0 for r in results),
        "ttft_s": max((r["ttft_s"] for r in started), default=None),
        "decode_tps": round(decoded / span, 2) if span > 0 else None,
        "e2e_s": round(time.perf_counter() - t0, 3),
        "finish_reason": reasons.pop() if len(reasons) == 1 else "mixed",
        "estimated": any(r["estimated"] for r in results),
    }


async def run(request):
    """One round: every prompt streams at the same time (one turret each), tagged by lane on one SSE stream."""
    app = request.app
    if app["lock"].locked():
        return web.json_response({"error": "busy"}, status=429)
    async with app["lock"]:
        body = await request.json()
        prompts = body.get("prompts") or [body.get("prompt", "")]
        prompts = [str(p).strip()[:4000] for p in prompts[: app["max_lanes"]]]
        if not all(prompts):
            return web.json_response({"error": "empty prompt"}, status=400)
        max_tokens = max(1, min(int(body.get("max_tokens") or app["max_tokens"]), app["max_tokens_cap"]))
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(request)
        write_lock = asyncio.Lock()
        t0 = time.perf_counter()

        async def send(**payload):
            async with write_lock:
                await emit(resp, **payload)

        async def lane(i, prompt):
            async def on_token(text, n, total, t):
                await send(type="token", lane=i, text=text, n=n, total=total, t=round(t, 4))

            result = await generate(app["backend"], prompt, max_tokens, on_token, t0)
            await send(type="lane_done", lane=i, **result)
            return result

        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(lane(i, p)) for i, p in enumerate(prompts)]
            summary = combine([t.result() for t in tasks], t0)
            await send(type="done", **summary)
            per_lane = [r["decode_tps"] for r in summary["lanes"]]
            print(
                f"[run] lanes={len(per_lane)} tokens={summary['completion_tokens']} decode_tps={summary['decode_tps']}"
                f" ttft_s={summary['ttft_s']} e2e_s={summary['e2e_s']} per_lane_tps={per_lane}",
                flush=True,
            )
        except* ConnectionResetError:
            print("[run] client disconnected, generation stopped", flush=True)
        except* Exception as group:
            e = group.exceptions[0]
            print(f"[run] error: {type(e).__name__}: {e}", flush=True)
            with contextlib.suppress(ConnectionResetError):
                await send(type="error", message=f"{type(e).__name__}: {e}")
        return resp


async def config(request):
    app = request.app
    return web.json_response({
        "backend": app["backend"].name,
        "model_label": app["model_label"],
        "hw_label": app["hw_label"],
        "max_tokens": app["max_tokens"],
        "max_lanes": app["max_lanes"],
    })


async def health(request):
    app = request.app
    ok, detail = await app["backend"].health()
    page = (ROOT / "index.html").stat()
    return web.json_response({
        "ok": ok,
        "detail": detail,
        "busy": app["lock"].locked(),
        "warming": app["state"]["warming"] > 0,
        "page": f"{page.st_mtime_ns:x}-{page.st_size:x}",
    })


async def index(request):
    return web.FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-cache"})


async def warmup(app):
    """First requests after launch compile Triton kernels; take that hit before visitors do."""
    backend = app["backend"]
    app["state"]["warming"] += 1
    try:
        async with app["lock"]:
            for _ in range(120):
                ok, detail = await backend.health()
                if ok:
                    break
                print(f"[warmup] waiting for backend: {detail}", flush=True)
                await asyncio.sleep(5)
            else:
                print("[warmup] backend never became healthy, skipping", flush=True)
                return
            for i in range(2):
                result = await generate(backend, "Introduce yourself in three sentences.", 64)
                print(f"[warmup] run {i + 1}: {result}", flush=True)
    except Exception as e:
        print(f"[warmup] failed: {type(e).__name__}: {e}", flush=True)
    finally:
        app["state"]["warming"] -= 1


async def warm_rounds(app, rounds):
    try:
        async with app["lock"]:
            for prompts in rounds:
                t = time.perf_counter()
                for _ in range(2):
                    await asyncio.gather(*(generate(app["backend"], p, 16) for p in prompts))
                print(f"[warmup] {len(prompts)} turret(s) from {prompts[0][:30]!r}: {time.perf_counter() - t:.1f}s", flush=True)
    except Exception as e:
        print(f"[warmup] failed: {type(e).__name__}: {e}", flush=True)
    finally:
        app["state"]["warming"] -= 1


async def warmup_page(request):
    """Prefill shapes depend on which prompts run together, and the first of each compiles Triton kernels for
    seconds. The page posts every turret round it can send; each runs once with a cold prefix cache, once warm."""
    app = request.app
    state = app["state"]
    if app["backend"].name != "sglang":
        return web.json_response({"queued": 0})
    body = await request.json()
    rounds = []
    for r in body.get("rounds") or []:
        prompts = tuple(str(p).strip()[:4000] for p in r[: app["max_lanes"]])
        if prompts and all(prompts) and prompts not in state["warmed"]:
            state["warmed"].add(prompts)
            rounds.append(prompts)
    print(f"[warmup] page sent {len(body.get('rounds') or [])} rounds, {len(rounds)} not yet warm", flush=True)
    if rounds:
        state["warming"] += 1
        task = asyncio.create_task(warm_rounds(app, rounds))
        state["tasks"].add(task)
        task.add_done_callback(state["tasks"].discard)
    return web.json_response({"queued": len(rounds)})


async def start_warmup(app):
    app["warmup_task"] = asyncio.create_task(warmup(app))


async def close_backend(app):
    session = getattr(app["backend"], "session", None)
    if session is not None:
        await session.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", choices=("mock", "claude", "sglang"), default="mock")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--max-tokens", type=int, default=192, help="tokens per run, and bricks per wall")
    p.add_argument("--max-tokens-cap", type=int, default=1024)
    p.add_argument("--max-lanes", type=int, default=4, help="most turrets per round; keep within SGLang's --max-running-requests")
    p.add_argument("--mock-rate", type=float, default=26.0, help="mock decode tok/s")
    p.add_argument("--mock-ttft", type=float, default=0.45, help="mock time to first token, seconds")
    p.add_argument("--claude-model", default="haiku")
    p.add_argument("--claude-bin", default=shutil.which("claude") or os.path.expanduser("~/.local/bin/claude"))
    p.add_argument("--sglang-url", default="http://127.0.0.1:30000")
    p.add_argument("--sglang-model", default="qwen38-awq", help="the server's --served-model-name")
    p.add_argument("--temperature", type=float, default=0.0, help="0 repeats the same text and speed per stage")
    p.add_argument("--model-label")
    p.add_argument("--hw-label")
    p.add_argument("--no-warmup", action="store_true")
    args = p.parse_args()

    if args.backend == "mock":
        backend = MockBackend(args.mock_rate, args.mock_ttft)
        labels = ("MOCK", "simulated tokens, no model")
    elif args.backend == "claude":
        backend = ClaudeBackend(args.claude_model, args.claude_bin)
        labels = (f"Claude {args.claude_model}", "cloud API, pipeline test only")
    else:
        backend = SGLangBackend(args.sglang_url, args.sglang_model, args.temperature)
        labels = ("Qwen3.8-27B · Quark INT4", "1× Ryzen AI MAX+ 395 · Radeon 8060S · local SGLang")

    app = web.Application()
    app["backend"] = backend
    app["lock"] = asyncio.Lock()
    app["state"] = {"warming": 0, "warmed": set(), "tasks": set()}
    app["max_tokens"] = args.max_tokens
    app["max_tokens_cap"] = args.max_tokens_cap
    app["max_lanes"] = max(1, args.max_lanes)
    app["model_label"] = args.model_label or labels[0]
    app["hw_label"] = args.hw_label or labels[1]
    app.router.add_get("/", index)
    app.router.add_get("/api/config", config)
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/run", run)
    app.router.add_post("/api/warmup", warmup_page)
    if args.backend == "sglang" and not args.no_warmup:
        app.on_startup.append(start_warmup)
    app.on_cleanup.append(close_backend)

    print(f"Token Breaker on http://localhost:{args.port}  backend={backend.name}", flush=True)
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
