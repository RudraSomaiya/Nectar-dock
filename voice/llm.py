"""Groq chat client: model fallback on rate limits/errors, tool calling, no reasoning text."""
import json
import time

import httpx

from common import S, log
from settings import groq_key

URL = "https://api.groq.com/openai/v1/chat/completions"
_cooldown = {}
_client = httpx.Client(timeout=45)


def _extra(model):
    if model.startswith("qwen/"):
        return {"reasoning_effort": "none"}
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "include_reasoning": False}
    return {}


def chat(messages, tools=None, temperature=0.3, max_tokens=1024, models=None, json_mode=False):
    """Returns the assistant message dict ({'content', 'tool_calls'})."""
    models = models or S["groq_models"]
    headers = {"Authorization": f"Bearer {groq_key()}"}
    last_err = None
    for model in models:
        if _cooldown.get(model, 0) > time.time():
            continue
        body = {"model": model, "messages": messages, "temperature": temperature,
                "max_completion_tokens": max_tokens, **_extra(model)}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        t = time.perf_counter()
        try:
            r = _client.post(URL, json=body, headers=headers)
        except httpx.HTTPError as e:
            last_err = str(e)
            log(f"llm {model} network error: {e}")
            continue
        ms = round((time.perf_counter() - t) * 1000)
        if r.status_code == 200:
            data = r.json()
            u = data.get("usage", {})
            log(f"llm ok {model} {ms}ms in={u.get('prompt_tokens')} out={u.get('completion_tokens')}")
            return data["choices"][0]["message"]
        last_err = f"{r.status_code} {r.text[:200]}"
        log(f"llm fail {model} {ms}ms {last_err}")
        if r.status_code in (429, 500, 502, 503, 504):
            try:
                wait = float(r.headers.get("retry-after", 20))
            except ValueError:
                wait = 20
            _cooldown[model] = time.time() + min(wait, 60)
        elif r.status_code == 400 and tools and "tool" in r.text.lower():
            # Model produced a malformed tool call; the next model usually manages.
            continue
    raise RuntimeError(f"Groq unavailable ({last_err})")


_online = {"at": 0.0, "ok": True}


def online():
    """Can we reach Groq? (Quick TCP check, remembered for 15 s.)"""
    import socket
    if time.time() - _online["at"] < 15:
        return _online["ok"]
    try:
        socket.create_connection(("api.groq.com", 443), timeout=0.8).close()
        ok = True
    except OSError:
        ok = False
    _online.update(at=time.time(), ok=ok)
    return ok


def text(system, user, **kw):
    msg = chat([{"role": "system", "content": system}, {"role": "user", "content": user}], **kw)
    return (msg.get("content") or "").strip()


def parse_args(call):
    try:
        return json.loads(call["function"].get("arguments") or "{}")
    except ValueError:
        return {}
