"""Installed apps (Start menu), running windows, and matching spoken names to them."""
import json
import os
import re
import subprocess
import threading
import time

import psutil
import win32con
import win32gui
import win32process
from rapidfuzz import fuzz, process

from common import S, data_path, log
import inject

INDEX_FILE = data_path("apps.json")
_index = []
_index_lock = threading.Lock()

# Process names for apps whose exe differs from their name.
EXE_HINTS = {"brave": "brave.exe", "chrome": "chrome.exe", "google chrome": "chrome.exe", "edge": "msedge.exe",
             "microsoft edge": "msedge.exe", "notepad": "notepad.exe", "file explorer": "explorer.exe",
             "explorer": "explorer.exe", "claude": "claude.exe", "spotify": "spotify.exe", "discord": "discord.exe",
             "whatsapp": "whatsapp.exe", "vs code": "code.exe", "visual studio code": "code.exe",
             "wispr flow": "wispr flow.exe", "task manager": "taskmgr.exe", "valorant": "valorant.exe",
             "steam": "steam.exe", "obs": "obs64.exe", "vlc": "vlc.exe", "telegram": "telegram.exe",
             "calculator": "calculatorapp.exe", "settings": "systemsettings.exe", "camera": "windowscamera.exe",
             "terminal": "windowsterminal.exe", "word": "winword.exe", "excel": "excel.exe",
             "powerpoint": "powerpnt.exe", "outlook": "olk.exe", "teams": "ms-teams.exe"}
NEVER_CLOSE = {"explorer.exe", "nectar.exe", "dwm.exe", "pythonw.exe", "python.exe", "msedgewebview2.exe"}
# The desktop and taskbar: sending these WM_CLOSE opens the shutdown dialog.
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def _norm(s):
    s = s.lower()
    s = re.sub(r"\b(app|application|browser|the|my|program)\b", " ", s)
    return re.sub(r"[^a-z0-9+#]+", " ", s).strip()


def resolve_alias(name):
    n = _norm(name)
    for heard, meant in S.get("aliases", {}).items():
        if n == _norm(heard):
            return meant
    return name


def refresh_index():
    """Read the Start menu's app list (Get-StartApps): names plus how to launch them."""
    global _index
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=30,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        items = json.loads(out)
        apps = [{"name": a["Name"], "id": a["AppID"]} for a in items
                if not re.search(r"uninstall|readme|documentation|help|release notes|website|manual|faq",
                                 a["Name"], re.I)]
        with _index_lock:
            _index = apps
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(apps, f)
        log(f"app index: {len(apps)} apps")
    except Exception as e:
        log(f"app index failed: {e}")


def load_index():
    global _index
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            _index = json.load(f)
        if time.time() - os.path.getmtime(INDEX_FILE) > 6 * 3600:
            threading.Thread(target=refresh_index, daemon=True).start()
    except (OSError, ValueError):
        refresh_index()


def find_app(name, min_score=72):
    """Best installed app for a spoken name, or None."""
    name = resolve_alias(name)
    q = _norm(name)
    if not q or not _index:
        return None
    names = {i: _norm(a["name"]) for i, a in enumerate(_index)}
    # Exact or starts-with wins over fuzzy.
    for i, n in names.items():
        if n == q:
            return _index[i]
    starts = [i for i, n in names.items() if n.startswith(q + " ") or n.split(" ")[0] == q]
    if starts:
        return _index[min(starts, key=lambda i: len(names[i]))]
    # Whole-name similarity (not partial matching: "a new tab" must not match
    # "Add a new TAP virtual ethernet adapter").
    def score(a, b, **_):
        return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))

    best = process.extractOne(q, names, scorer=score)
    if best and best[1] >= min_score:
        return _index[best[2]]
    return None


def open_app(name):
    app = find_app(name)
    if app:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['id']}"])
        return f"Opened {app['name']}."
    # Maybe it's a command or exe on PATH (notepad, calc, mspaint...).
    try:
        os.startfile(name)
        return f"Opened {name}."
    except OSError:
        return f"I couldn't find an app called {name}."


def windows():
    """Visible top-level windows: [{'hwnd','title','exe','pid'}], most recent first (z-order)."""
    out = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex & win32con.WS_EX_TOOLWINDOW:
            return True
        cls = win32gui.GetClassName(hwnd)
        if cls in SHELL_CLASSES:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe = psutil.Process(pid).name()
        except Exception:
            return True
        if exe.lower() in ("textinputhost.exe", "applicationframehost.exe") and not title.strip():
            return True
        out.append({"hwnd": hwnd, "title": title, "exe": exe, "pid": pid, "cls": cls})
        return True

    win32gui.EnumWindows(cb, None)
    return out


def match_windows(name):
    """Running windows that belong to the spoken app name."""
    name = resolve_alias(name)
    q = _norm(name)
    hint = EXE_HINTS.get(q)
    app = find_app(name)
    wins = windows()
    hits = []
    for w in wins:
        exe = w["exe"].lower()
        stem = _norm(exe.rsplit(".", 1)[0])
        title = _norm(w["title"])
        score = 0
        if hint and exe == hint:
            score = 100
        elif stem == q or (len(q) > 2 and q in stem):
            score = 95
        elif app and _norm(app["name"]) and (_norm(app["name"]) in title or stem in _norm(app["name"])):
            score = 85
        elif fuzz.partial_ratio(q, title) >= 90 and len(q) > 3:
            score = 75
        if score:
            hits.append((score, w))
    hits.sort(key=lambda x: -x[0])
    if not hits:
        return []
    top = hits[0][0]
    return [w for s, w in hits if s == top]


def close_windows(wins):
    n = 0
    for w in wins:
        if w.get("cls") in SHELL_CLASSES:
            continue
        # File Explorer folder windows are fine to close; the rest of explorer.exe is not.
        if w["exe"].lower() in NEVER_CLOSE and w.get("cls") != "CabinetWClass":
            continue
        win32gui.PostMessage(w["hwnd"], win32con.WM_CLOSE, 0, 0)
        n += 1
    return n


def close_app(name, scope="all"):
    wins = match_windows(name)
    if not wins:
        # Tray-only apps have no window: end the process instead.
        q = _norm(resolve_alias(name))
        hint = EXE_HINTS.get(q, "")
        procs = [p for p in psutil.process_iter(["name"])
                 if p.info["name"] and (p.info["name"].lower() == hint or
                                        _norm(p.info["name"].rsplit(".", 1)[0]) == q or
                                        (len(q) > 3 and q.replace(" ", "") in p.info["name"].lower().replace(" ", "")))]
        procs = [p for p in procs if p.info["name"].lower() not in NEVER_CLOSE]
        if not procs:
            return f"{name} doesn't seem to be open."
        for p in procs:
            try:
                p.terminate()
            except psutil.Error:
                pass
        return f"Closed {procs[0].info['name'].rsplit('.', 1)[0]}."
    if scope == "one":
        wins = wins[:1]
    n = close_windows(wins)
    label = wins[0]["exe"].rsplit(".", 1)[0]
    return f"Closed {label}." if n == 1 else f"Closed {n} {label} windows."


def close_all(except_names=()):
    keep = set()
    for n in except_names:
        keep.update(w["hwnd"] for w in match_windows(n))
    wins = [w for w in windows() if w["hwnd"] not in keep and
            (w["exe"].lower() not in NEVER_CLOSE or w.get("cls") == "CabinetWClass")]
    return f"Closed {close_windows(wins)} windows."


def focus_app(name):
    wins = match_windows(name)
    if not wins:
        return None
    inject.focus(wins[0]["hwnd"])
    return f"Switched to {wins[0]['exe'].rsplit('.', 1)[0]}."


def list_open():
    seen, names = set(), []
    for w in windows():
        n = w["exe"].rsplit(".", 1)[0]
        if n.lower() not in seen and w["exe"].lower() not in NEVER_CLOSE:
            seen.add(n.lower())
            names.append(n)
    return names
