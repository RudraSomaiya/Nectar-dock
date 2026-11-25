"""Microphone recording while a key is held (16 kHz mono float32)."""
import collections
import re
import threading

import numpy as np
import sounddevice as sd

from common import S, log

RATE = 16000


def _device():
    want = (S.get("mic") or "").lower()
    if not want:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and want in d["name"].lower():
            return i
    log(f"mic '{S['mic']}' not found, using default")
    return None


_last_refresh = [0.0]


def _refresh_devices():
    """PortAudio only sees devices that existed when it started. Re-scan (cheap, ~50 ms) if it's been a
    while, so earphones plugged in later are found."""
    import time
    if time.time() - _last_refresh[0] > 20:
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:
            log(f"audio re-scan failed: {e}")
        _last_refresh[0] = time.time()


def full_name(name):
    """The old MME audio API cuts names at 31 characters; find the full one from WASAPI."""
    try:
        apis = sd.query_hostapis()
        stem = name.strip()[:28].lower()
        for d in sd.query_devices():
            if d["max_input_channels"] > 0 and "WASAPI" in apis[d["hostapi"]]["name"] and \
                    d["name"].lower().startswith(stem):
                return d["name"]
    except Exception:
        pass
    return name


def short_name(name):
    """'Microphone (AB13X USB Audio)' -> 'AB13X USB Audio'."""
    name = full_name(name).strip()
    if name.lower().startswith("microphone array"):
        return "Laptop mic"
    m = re.match(r"^(?:Microphone|Headset Microphone|Headset|Mic)\s*\((.+)\)$", name)
    return m.group(1) if m else name


def current_mic():
    """Name of the mic voice records from (your setting, else the Windows default input)."""
    try:
        idx = _device()
        d = sd.query_devices(idx if idx is not None else sd.default.device[0])
        return d["name"]
    except Exception:
        return "unknown microphone"


def list_mics():
    """Real input devices, once each (Windows lists every mic under several audio APIs)."""
    seen, out = set(), []
    try:
        apis = sd.query_hostapis()
        for d in sd.query_devices():
            if d["max_input_channels"] <= 0:
                continue
            if "MME" not in apis[d["hostapi"]]["name"]:
                continue
            key = short_name(d["name"]).lower()[:28]
            if key not in seen and "sound mapper" not in key:
                seen.add(key)
                out.append(d["name"])
    except Exception as e:
        log(f"listing mics failed: {e}")
    return out


class Recorder:
    def __init__(self):
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()
        self.level = 0.0
        self.recent = collections.deque(maxlen=8)
        self.mic = ""

    def start(self):
        with self._lock:
            self._chunks = []
            self.recent.clear()
            try:
                _refresh_devices()
                dev = _device()
                self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                              device=dev, callback=self._cb, blocksize=800)
                self._stream.start()
                self.mic = short_name(sd.query_devices(dev if dev is not None else sd.default.device[0])["name"])
            except Exception as e:
                self._stream = None
                raise RuntimeError(f"couldn't open the microphone: {e}")

    def _cb(self, data, frames, t, status):
        self._chunks.append(data[:, 0].copy())
        rms = float(np.sqrt(np.mean(data ** 2)))
        self.level = rms
        self.recent.append(rms)

    def bars(self, n=5):
        """The last n block levels as 0..1 (log scale: about -55 dB silent, -15 dB loud)."""
        vals = list(self.recent)[-n:]
        vals = [0.0] * (n - len(vals)) + vals
        return [min(1.0, max(0.04, (20 * np.log10(max(v, 1e-6)) + 55) / 40)) for v in vals]

    def snapshot(self) -> np.ndarray:
        """Audio recorded so far, without stopping (for the live transcript)."""
        chunks = list(self._chunks)
        return np.concatenate(chunks) if chunks else np.zeros(0, np.float32)

    def stop(self) -> np.ndarray:
        with self._lock:
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            return np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)


def has_speech(pcm: np.ndarray) -> bool:
    """Cheap check so silent key presses don't wake the speech worker."""
    if len(pcm) < RATE * 0.25:
        return False
    frames = pcm[: len(pcm) // 320 * 320].reshape(-1, 320)
    rms = np.sqrt((frames ** 2).mean(1))
    floor = np.percentile(rms, 10)
    return int((rms > max(0.006, floor * 4)).sum()) >= 10
