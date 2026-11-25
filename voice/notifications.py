"""Windows notifications in the notch.

Windows keeps toast notifications in a small SQLite database in your profile
(%LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db). We read it (read-only) every
1.5 s and show each new toast in the notch for a few seconds. Settings > Voice > Show notifications.
"""
import os
import re
import sqlite3
import threading
import time
from html import unescape

from common import log
import notch

DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Notifications\wpndatabase.db")
FRIENDLY = {"whatsapp": "WhatsApp", "teams": "Teams", "discord": "Discord", "claude": "Claude", "brave": "Brave",
            "chrome": "Chrome", "msedge": "Edge", "outlook": "Outlook", "windowslive.mail": "Mail",
            "telegram": "Telegram", "spotify": "Spotify", "slack": "Slack", "todos": "To Do", "netflix": "Netflix",
            "instagram": "Instagram", "phone": "Phone Link", "yourphone": "Phone Link", "notion": "Notion"}
SKIP = re.compile(r"widgets|webexperience|windows\.systemtoast\.(securityandmaintenance|backgroundaccess)", re.I)


def _app_name(aumid):
    low = (aumid or "").lower()
    for k, v in FRIENDLY.items():
        if k in low:
            return v
    a = aumid or "app"
    if "\\" in a:  # desktop apps registered by path: ...\WindowsPowerShell\v1.0\powershell.exe
        name = os.path.splitext(a.replace("/", "\\").split("\\")[-1])[0]
    elif a.lower().startswith("windows.systemtoast."):
        name = a.split(".", 2)[2]
    else:
        name = re.split(r"[!_]", a)[0].split(".")[-1]
    name = re.sub(r"(Desktop|App)$", "", name) or "Notification"
    return name[:1].upper() + name[1:]


def _texts(payload):
    p = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload or "")
    return [unescape(re.sub(r"<[^>]+>", "", t)).strip() for t in re.findall(r"<text[^>]*>(.*?)</text>", p, re.S)]


def _rows(con, after):
    return con.execute("""select n.Id, h.PrimaryId, n.Payload from Notification n
                          join NotificationHandler h on h.RecordId = n.HandlerId
                          where n.Id > ? and n.Type = 'toast' order by n.Id""", (after,)).fetchall()


def watch(is_busy):
    """Run forever. `is_busy()` returns True while voice is listening/working (notices wait)."""
    from settings import nectar_setting
    last = None
    pending = []
    while True:
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
            try:
                if last is None:  # start from now: don't replay old notifications
                    last = con.execute("select coalesce(max(Id), 0) from Notification").fetchone()[0]
                for rid, aumid, payload in _rows(con, last):
                    last = max(last, rid)
                    if SKIP.search(aumid or ""):
                        continue
                    texts = [t for t in _texts(payload) if t]
                    if texts:
                        pending.append((_app_name(aumid), texts))
            finally:
                con.close()
        except sqlite3.Error as e:
            log(f"notifications: {e}")
        if pending and not is_busy() and nectar_setting("nectar-voice-notifications", "true") != "false":
            app, texts = pending.pop(0)
            title, body = texts[0], " ".join(texts[1:])
            text = f"{title}: {body}" if body else title
            log(f"notification from {app} ({len(text)} chars)")
            notch.state("notice", "command", text, label=app, hold_ms=min(9000, 4000 + len(text) * 40))
            time.sleep(1.0)
            continue
        time.sleep(1.5)


def start(is_busy):
    threading.Thread(target=watch, args=(is_busy,), daemon=True).start()
