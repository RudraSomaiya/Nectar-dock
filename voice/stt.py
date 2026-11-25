"""Client for stt_worker.py: starts it on demand, sends audio, gets text."""
import os
import subprocess
import sys
import threading
import time

import httpx
import numpy as np
import psutil

from common import S, data_path, log

HERE = os.path.dirname(os.path.abspath(__file__))
PORT_FILE = data_path("stt.port")
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
_lock = threading.Lock()
_proc = None


def _base():
    try:
        with open(PORT_FILE) as f:
            return f"http://127.0.0.1:{int(f.read().strip())}"
    except (OSError, ValueError):
        return None


def _alive():
    base = _base()
    if not base:
        return False
    try:
        return httpx.get(base + "/health", timeout=0.5).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_started():
    """Start the worker if it isn't running. Returns immediately; the model loads in the background."""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None and _alive():
            return
        if _alive():
            return
        try:
            os.remove(PORT_FILE)
        except FileNotFoundError:
            pass
        exe = PYW if os.path.exists(PYW) else sys.executable
        _proc = subprocess.Popen([exe, os.path.join(HERE, "stt_worker.py")], cwd=HERE,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        log(f"stt worker started (pid {_proc.pid})")


def transcribe_partial(pcm: np.ndarray, prompt="") -> str:
    """Quick pass over the audio so far, for the live transcript. '' on any problem."""
    base = _base()
    if not base:
        return ""
    try:
        r = httpx.post(base + "/transcribe", content=pcm.astype(np.float32).tobytes(),
                       params={"language": S["language"], "prompt": prompt}, timeout=4)
        return r.json().get("text", "") if r.status_code == 200 else ""
    except (httpx.HTTPError, ValueError):
        return ""


def transcribe(pcm: np.ndarray, prompt="") -> str:
    ensure_started()
    deadline = time.time() + 30
    while not _base() and time.time() < deadline:
        time.sleep(0.05)
    base = _base()
    if not base:
        raise RuntimeError("speech worker didn't start")
    for attempt in range(2):
        try:
            r = httpx.post(base + "/transcribe", content=pcm.astype(np.float32).tobytes(),
                           params={"language": S["language"], "prompt": prompt}, timeout=90)
            r.raise_for_status()
            return r.json()["text"]
        except httpx.HTTPError as e:
            if attempt:
                raise RuntimeError(f"speech worker failed: {e}")
            log(f"stt retry after {e}")
            time.sleep(0.6)  # let a crashed worker exit first
            ensure_started()
            deadline = time.time() + 30
            while not _base() and time.time() < deadline:
                time.sleep(0.05)
            base = _base() or base


def ready():
    """True when the worker is up and its model is loaded (cheap; used for live transcripts)."""
    base = _base()
    if not base:
        return False
    try:
        return bool(httpx.get(base + "/health", timeout=0.3).json().get("ready"))
    except (httpx.HTTPError, ValueError):
        return False


def worker_ram_mb():
    """RAM of the real worker process (the venv python.exe is a launcher with a child)."""
    total = 0
    for p in psutil.process_iter(["cmdline"]):
        try:
            if any("stt_worker.py" in c for c in (p.info["cmdline"] or [])):
                total = max(total, p.memory_info().rss)
        except (psutil.Error, TypeError):
            pass
    return round(total / 2**20)
