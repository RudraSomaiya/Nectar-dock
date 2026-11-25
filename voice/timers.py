"""Timers, reminders and a stopwatch. Reminders survive a restart (saved to reminders.json)."""
import datetime as dt
import json
import threading
import time
import winsound

from common import data_path, log
import notch

_timers = {}  # id -> {"label", "ends", "timer"}
_next_id = [1]
_lock = threading.Lock()
_stopwatch = {"start": None}
REMINDERS_FILE = data_path("reminders.json")


def _fmt(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} h")
    if m:
        parts.append(f"{m} min")
    if s and not h:
        parts.append(f"{s} s")
    return " ".join(parts) or "0 s"


def _ring(label):
    log(f"timer fired: {label}")
    notch.done(f"⏰ {label}")
    for _ in range(3):
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        time.sleep(0.6)


def _schedule(seconds, label, kind="timer", save=True):
    with _lock:
        tid = _next_id[0]
        _next_id[0] += 1
        t = threading.Timer(seconds, lambda: _fire(tid))
        t.daemon = True
        _timers[tid] = {"label": label, "ends": time.time() + seconds, "timer": t, "kind": kind}
        t.start()
    if save:
        _save()
    return tid


def _fire(tid):
    with _lock:
        item = _timers.pop(tid, None)
    if item:
        _save()
        _ring(item["label"] if item["kind"] == "reminder" else f"Timer done{': ' + item['label'] if item['label'] else ''}")


def start_timer(seconds, label=""):
    _schedule(seconds, label)
    return f"Timer set for {_fmt(seconds)}{' (' + label + ')' if label else ''}."


def remind(text, seconds=None, at=None):
    """Remind in `seconds`, or at a clock time 'HH:MM' (24 h) today/tomorrow."""
    if at:
        h, m = (int(x) for x in at.split(":")[:2])
        now = dt.datetime.now()
        when = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if when <= now:
            when += dt.timedelta(days=1)
        seconds = (when - now).total_seconds()
        _schedule(seconds, text, "reminder")
        return f"I'll remind you at {when.strftime('%I:%M %p').lstrip('0')}: {text}."
    _schedule(seconds, text, "reminder")
    return f"I'll remind you in {_fmt(seconds)}: {text}."


def list_timers():
    with _lock:
        items = sorted(_timers.values(), key=lambda x: x["ends"])
    if not items:
        return "No timers or reminders running."
    return "; ".join(f"{i['label'] or 'timer'} in {_fmt(i['ends'] - time.time())}" for i in items) + "."


def cancel(which="all"):
    """Cancel 'all', 'last', or the timer/reminder whose label matches."""
    with _lock:
        if not _timers:
            return "There's nothing to cancel."
        if which in ("all", "", None):
            ids = list(_timers)
        elif which == "last":
            ids = [max(_timers)]
        else:
            ids = [i for i, t in _timers.items() if which.lower() in (t["label"] or "").lower()] or [max(_timers)]
        for i in ids:
            _timers.pop(i)["timer"].cancel()
    _save()
    return "Cancelled." if len(ids) == 1 else f"Cancelled {len(ids)}."


def current_countdown():
    """Seconds left on the soonest running countdown (for re-showing it after Nectar restarts)."""
    with _lock:
        left = [t["ends"] - time.time() for t in _timers.values() if t["kind"] == "timer" and t.get("paused") is None]
    left = [x for x in left if x > 1]
    return min(left) if left else None


def pause_all(pause=True):
    """Pause (or resume) countdown timers; reminders keep running."""
    with _lock:
        for tid, t in list(_timers.items()):
            if t["kind"] != "timer":
                continue
            if pause and not t.get("paused"):
                t["timer"].cancel()
                t["paused"] = max(0.0, t["ends"] - time.time())
            elif not pause and t.get("paused") is not None:
                left = t.pop("paused")
                t["ends"] = time.time() + left
                t["timer"] = threading.Timer(left, lambda i=tid: _fire(i))
                t["timer"].daemon = True
                t["timer"].start()


def stopwatch(action):
    if action == "start":
        _stopwatch["start"] = time.time()
        return "Stopwatch started."
    if not _stopwatch["start"]:
        return "The stopwatch isn't running."
    elapsed = _fmt(time.time() - _stopwatch["start"])
    if action == "stop":
        _stopwatch["start"] = None
        return f"Stopped at {elapsed}."
    return f"{elapsed} so far."


def _save():
    with _lock:
        items = [{"label": t["label"], "ends": t["ends"], "kind": t["kind"]} for t in _timers.values()]
    try:
        with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f)
    except OSError:
        pass


def load_saved():
    try:
        with open(REMINDERS_FILE, encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        return
    for it in items:
        left = it["ends"] - time.time()
        kind = it.get("kind", "reminder")
        if kind == "timer" and left < -60:
            continue  # a countdown that ended long ago isn't worth ringing for
        _schedule(max(1, left), it["label"] + (" (missed while voice was off)" if left < 0 else ""), kind,
                  save=False)
