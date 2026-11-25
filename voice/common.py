"""Shared bits: settings, logging, paths."""
import os
import threading
import time

from settings import load_settings

S = load_settings()
os.makedirs(S["data_dir"], exist_ok=True)
os.makedirs(S["log_dir"], exist_ok=True)
_log_lock = threading.Lock()
LOG_FILE = os.path.join(S["log_dir"], "voice.log")


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    with _log_lock:
        try:
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2_000_000:
                os.replace(LOG_FILE, LOG_FILE + ".1")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def data_path(*parts):
    return os.path.join(S["data_dir"], *parts)
