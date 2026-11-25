"""Speech-to-text worker: Whisper large-v3-turbo on the GPU, only while it is needed.

voice-core starts this process when a hotkey is pressed, so the model loads while
the user is still talking. It quits after `idle_exit_s` without a request, which is
the only way to hand back the ~700 MB of RAM the CUDA libraries take (unloading the
model inside the process leaves them resident).

Protocol (127.0.0.1 only, port written to <data_dir>/stt.port):
  GET  /health            -> {"ready": bool, "device": ..., "load_s": ...}
  POST /transcribe        body: raw 16 kHz mono float32 PCM
                          query: language=en (optional), prompt=... (optional)
                          -> {"text": ..., "ms": ...}
  POST /quit
"""
import ctypes
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from settings import load_settings  # noqa: E402

S = load_settings()
CUDA_DIR = S["cuda_dir"]
if os.path.isdir(CUDA_DIR):
    os.add_dll_directory(CUDA_DIR)
    os.environ["PATH"] = CUDA_DIR + os.pathsep + os.environ.get("PATH", "")

import numpy as np  # noqa: E402

state = {"ready": False, "device": None, "load_s": None, "error": None, "last_use": time.time()}
model = None
model_lock = threading.Lock()


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + "stt: " + msg
    print(line, flush=True)
    try:
        os.makedirs(S["log_dir"], exist_ok=True)
        with open(os.path.join(S["log_dir"], "stt.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load():
    global model
    t = time.perf_counter()
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(S["whisper_model"], device="cuda", compute_type="float16")
        state["device"] = "cuda"
    except Exception as e:  # GPU off (Eco mode) or CUDA missing
        log(f"cuda failed ({e}); falling back to cpu int8")
        model = WhisperModel(S["whisper_model"], device="cpu", compute_type="int8",
                             cpu_threads=max(2, (os.cpu_count() or 8) // 2))
        state["device"] = "cpu"
    # Warm up so the first real request doesn't pay for cuDNN initialisation.
    list(model.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1)[0])
    state["load_s"] = round(time.perf_counter() - t, 2)
    state["ready"] = True
    state["last_use"] = time.time()
    log(f"ready on {state['device']} in {state['load_s']} s")


def transcribe(pcm, language, prompt):
    with model_lock:
        segs, _info = model.transcribe(
            pcm,
            language=language or None,
            beam_size=S["beam_size"],
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt=prompt or None,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segs).strip()


def trim_working_set():
    """Give resident pages back to Windows; they fault back in on next use."""
    try:
        k32 = ctypes.windll.kernel32
        k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            return self._json(200, {k: state[k] for k in ("ready", "device", "load_s", "error")})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/quit":
            self._json(200, {"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
            return
        if url.path != "/transcribe":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        pcm = np.frombuffer(self.rfile.read(n), dtype=np.float32)
        q = parse_qs(url.query)
        # Wait for the model if the request arrived while it was still loading.
        deadline = time.time() + 60
        while not state["ready"] and time.time() < deadline and not state["error"]:
            time.sleep(0.05)
        if not state["ready"]:
            return self._json(503, {"error": state["error"] or "model not ready"})
        t = time.perf_counter()
        try:
            text = transcribe(pcm, q.get("language", [S["language"]])[0], q.get("prompt", [""])[0])
        except Exception as e:
            log(f"transcribe failed: {e}")
            self._json(500, {"error": str(e)})
            if state["device"] == "cuda":
                # The GPU probably went away (Eco mode). Quit; the client starts a new worker, which
                # falls back to the CPU, and retries this audio.
                log("CUDA error: exiting so a fresh worker can start (CPU if the GPU is gone)")
                threading.Thread(target=lambda: (time.sleep(0.2), os._exit(1)), daemon=True).start()
            return
        state["last_use"] = time.time()
        ms = round((time.perf_counter() - t) * 1000)
        log(f"{len(pcm) / 16000:.1f}s audio -> {len(text)} chars in {ms} ms")
        self._json(200, {"text": text, "ms": ms, "device": state["device"]})


def gpu_present():
    """Is the NVIDIA GPU present and working right now? (In G-Helper Eco mode it leaves the device list.)"""
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        wmi = win32com.client.GetObject("winmgmts:")
        rows = wmi.ExecQuery("select ConfigManagerErrorCode from Win32_VideoController where Name like '%NVIDIA%'")
        return any(r.ConfigManagerErrorCode == 0 for r in rows)
    except Exception:
        return False


def idle_watch():
    trimmed = False
    last_gpu_check = 0
    while True:
        time.sleep(5)
        idle = time.time() - state["last_use"]
        # Loaded on the CPU because the GPU was off? Once it's back, restart while idle: the next
        # request starts a fresh worker, which loads on the GPU.
        if state["ready"] and state["device"] == "cpu" and time.time() - last_gpu_check > 60:
            last_gpu_check = time.time()
            if gpu_present() and idle > 3 and not model_lock.locked():
                log("GPU is available again; exiting so the next request loads on the GPU")
                os._exit(0)
        from settings import nectar_setting
        try:  # Settings > Voice > Unload when idle (0 = never)
            limit = int(nectar_setting("nectar-voice-idle", S["stt_idle_exit_s"]))
        except (TypeError, ValueError):
            limit = S["stt_idle_exit_s"]
        if limit > 0 and state["ready"] and idle > limit:
            log(f"idle {int(idle)} s, exiting to free memory")
            os._exit(0)
        if state["ready"] and idle > 30 and not trimmed:
            trim_working_set()
            trimmed = True
        elif idle <= 30:
            trimmed = False


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    os.makedirs(S["data_dir"], exist_ok=True)
    with open(os.path.join(S["data_dir"], "stt.port"), "w") as f:
        f.write(str(port))
    log(f"listening on {port}, pid {os.getpid()}")

    def _load():
        try:
            load()
        except Exception as e:
            state["error"] = str(e)
            log(f"load failed: {e}")

    threading.Thread(target=_load, daemon=True).start()
    threading.Thread(target=idle_watch, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
