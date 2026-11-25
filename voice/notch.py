"""Talk to Nectar's notch through its local API (127.0.0.1 + token in agent.json).

/voice drives the voice view (listening, live transcript, result) and Nectar's own timer
and stopwatch. /ask puts Allow / Deny in the notch for actions that need a yes.
Everything fails quietly: if Nectar is closed, voice still works.
"""
import json
import os
import queue
import threading

import httpx

from common import log

NAME = "Gizmo"
CFG = os.path.join(os.environ.get("APPDATA", ""), "com.kua8.nectar", "agent.json")
_q = queue.Queue()
_client = httpx.Client(timeout=2)


def _cfg():
    try:
        with open(CFG, encoding="utf-8") as f:
            c = json.load(f)
        return f"http://127.0.0.1:{c['port']}", {"Authorization": f"Bearer {c['token']}"}
    except (OSError, ValueError, KeyError):
        return None, None


def _send(body):
    base, headers = _cfg()
    if not base:
        return False
    try:
        return _client.post(base + "/voice", json=body, headers=headers).status_code == 204
    except httpx.HTTPError:
        return False


def _worker():
    # One sender thread keeps updates in order (a late "listening" can't overwrite a result).
    while True:
        body = _q.get()
        # Collapse a backlog of the same kind of update (state, level) to the newest one.
        while body.get("kind") in ("state", "level") and not _q.empty():
            nxt = _q.queue[0]
            if nxt.get("kind") != body.get("kind"):
                break
            body = _q.get()
        _send(body)


threading.Thread(target=_worker, daemon=True).start()


def state(phase, mode="command", text="", label=None, hold_ms=None, icon=None):
    body = {"kind": "state", "state": phase, "mode": mode, "text": text[:500]}
    if label:
        body["label"] = label
    if icon and len(icon) < 6500:  # Nectar takes up to 8 KB per message
        body["icon"] = icon
    if hold_ms:
        body["hold_ms"] = hold_ms
    _q.put(body)


def listening(mode, text="", mic=""):
    state("listening", mode, text, label=f"Listening · 🎙 {mic}" if mic else None)


def levels(values):
    """Live mic level bars (0..1 each) while you talk."""
    _q.put({"kind": "level", "values": [round(float(v), 2) for v in values]})


def transcribing(mode, text=""):
    state("transcribing", mode, text)


def thinking(text, mode="command"):
    state("thinking", mode, text)


def done(text, mode="command"):
    # Longer answers stay up longer so there's time to read them.
    state("result", mode, text, hold_ms=min(20000, 3500 + len(text) * 60))


def error(text, mode="command"):
    state("error", mode, text, hold_ms=6000)


def clear():
    state("idle")


def timer(action, seconds=None):
    """Drive Nectar's countdown (it shows in the clock and as a ring). True if Nectar took it."""
    body = {"kind": "timer", "action": action}
    if seconds:
        body["seconds"] = seconds
    return _send(body)


def stopwatch(action):
    return _send({"kind": "stopwatch", "action": action})


def ask(question, detail="", choices=None, timeout=120):
    """Allow/Deny (or choices) in the notch. True/False, a choice index, or None if unanswered."""
    base, headers = _cfg()
    if not base:
        return None
    state("thinking", "command", f"{question} Hover the notch to answer.", label="Needs your OK")
    body = {"name": NAME, "tool": question[:60], "detail": detail[:600]}
    if choices:
        body["questions"] = [{"question": question, "options": choices[:4]}]
    try:
        r = httpx.post(base + "/ask", json=body, headers=headers, timeout=timeout + 5)
        if r.status_code != 200:
            return None
        data = r.json()
        if choices:
            return data.get("choices", [None])[0] if data.get("decision") == "allow" else None
        return data.get("decision") == "allow"
    except (httpx.HTTPError, ValueError) as e:
        log(f"notch ask failed: {e}")
        return None
    finally:
        # The question used the agent panel; put the notch back to normal afterwards.
        try:
            _client.post(base + "/activity", json={"state": "clear", "name": NAME}, headers=headers)
        except httpx.HTTPError:
            pass
