"""Claude plan usage (5-hour and weekly limits) for Nectar's "Claude usage" widget.

Uses the sign-in Claude Code keeps in ~/.claude/.credentials.json and asks Anthropic's usage
endpoint, the same numbers Claude Code's /usage shows. The token never leaves this PC except to
api.anthropic.com. We never refresh the token ourselves: refresh tokens rotate, and a background
refresh could sign Claude Code out. When it has expired the widget says so until Claude Code
refreshes it (any Claude Code use does).
"""
import json
import os
import threading
import time

import httpx

from common import log
import notch

CREDS = os.path.join(os.environ.get("USERPROFILE", ""), ".claude", ".credentials.json")
URL = "https://api.anthropic.com/api/oauth/usage"
EVERY_S = 300


def fetch():
    try:
        with open(CREDS, encoding="utf-8") as f:
            oauth = json.load(f).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return {"status": "not signed in to Claude Code"}
    token, expires = oauth.get("accessToken"), (oauth.get("expiresAt") or 0) / 1000
    if not token:
        return {"status": "not signed in to Claude Code"}
    if expires and expires < time.time() + 30:
        return {"status": "expired"}
    try:
        r = httpx.get(URL, timeout=10, headers={"Authorization": f"Bearer {token}",
                                                "anthropic-beta": "oauth-2025-04-20"})
    except httpx.HTTPError as e:
        return {"status": f"offline ({e.__class__.__name__})"}
    if r.status_code == 401:
        return {"status": "expired"}
    if r.status_code != 200:
        return {"status": f"error {r.status_code}"}
    d = r.json()
    out = {}
    for key, name in (("five_hour", "five_hour"), ("seven_day", "seven_day")):
        part = d.get(key) or {}
        if part.get("utilization") is not None:
            out[name] = float(part["utilization"])
            if part.get("resets_at"):
                out[f"{name}_resets"] = part["resets_at"]
    return out or {"status": "no usage data"}


def loop():
    from settings import nectar_setting
    while True:
        # Off unless you switch it on (Settings › Gizmo › Claude usage widget): it reads your Claude sign-in.
        if nectar_setting("nectar-voice-claude-usage", "false") != "true":
            time.sleep(30)
            continue
        try:
            data = fetch()
        except Exception as e:  # never let the widget kill voice
            data = {"status": f"error ({e.__class__.__name__})"}
        notch._send({"kind": "claude-usage", **data})
        log(f"claude usage: {json.dumps({k: v for k, v in data.items() if not k.endswith('resets')})}")
        time.sleep(EVERY_S)


def start():
    threading.Thread(target=loop, daemon=True).start()
