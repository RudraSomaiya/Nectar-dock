"""Nectar Voice: hold Right Ctrl for a command, Right Alt to dictate.

  pythonw core.py                  run in the background (normal use)
  python  core.py --say "open notepad"        test a command without the mic
  python  core.py --dictate "hello comma world" test dictation text processing (prints, doesn't type)
  python  core.py --wav file.wav [--mode command|dictate]  run a recording through the whole pipeline
"""
import argparse
import os
import sys
import threading
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import keyboard  # noqa: E402
import numpy as np  # noqa: E402

import answers  # noqa: E402
import apps  # noqa: E402
import audio  # noqa: E402
import brain  # noqa: E402
import inject  # noqa: E402
import notch  # noqa: E402
import stt  # noqa: E402
import textops  # noqa: E402
import timers  # noqa: E402
from common import S, log  # noqa: E402


# Names the keyboard library may report for the same physical key (AltGr layouts, etc.).
KEY_ALIASES = {"right alt": {"right alt", "alt gr", "altgr", "right menu"},
               "right ctrl": {"right ctrl", "right control"}}
MODIFIERS = {"ctrl", "left ctrl", "right ctrl", "alt", "left alt", "right alt", "alt gr", "shift", "left shift",
             "right shift", "left windows", "right windows"}


class Voice:
    def __init__(self):
        self.rec = audio.Recorder()
        self.mode = None          # "command" / "dictate" while a key is held
        self.held_key = None
        self.pressed_at = 0
        self.window = {}
        self.other_key = False    # another key pressed during the hold = it was a shortcut
        self.busy = threading.Lock()
        self.keys = {}
        for key, mode in ((S["command_key"], "command"), (S["dictate_key"], "dictate")):
            for alias in KEY_ALIASES.get(key, {key}):
                self.keys[alias] = mode

    # -- hotkeys ------------------------------------------------------------------------
    def on_key(self, e):
        name = (e.name or "").lower()
        if e.event_type == keyboard.KEY_DOWN:
            if name in self.keys:
                if self.mode is None:
                    self.held_key = name
                    self.start(self.keys[name])
                return
            # AltGr sends a fake Ctrl, and Shift etc. don't make it a shortcut; only real keys do.
            if self.mode is not None and name not in MODIFIERS:
                self.other_key = True
        elif e.event_type == keyboard.KEY_UP and self.mode is not None and name in self.keys \
                and self.keys[name] == self.mode:
            self.finish()

    def start(self, mode):
        self.mode, self.other_key, self.pressed_at = mode, False, time.time()
        self.window = inject.window_info()
        try:
            self.rec.start()
        except RuntimeError as err:
            self.mode = None
            log(str(err))
            notch.error("Couldn't open the microphone.")
            return
        stt.ensure_started()  # loads the model while you talk, if it had gone idle
        threading.Thread(target=self._live, args=(self.pressed_at, mode), daemon=True).start()

    def _live(self, stamp, mode):
        """While the key is held: show 'Listening', then the words so far, about once a second."""
        time.sleep(S["min_hold_ms"] / 1000)
        if self.pressed_at != stamp or not self.mode or self.other_key:
            return
        mic = self.rec.mic
        notch.listening(mode, mic=mic)
        threading.Thread(target=self._levels, args=(stamp,), daemon=True).start()
        shown, interval = "", 0.8
        prompt = ", ".join(S.get("vocabulary", []))
        while self.pressed_at == stamp and self.mode and not self.other_key:
            time.sleep(interval)
            if not (self.pressed_at == stamp and self.mode):
                break
            pcm = self.rec.snapshot()
            if len(pcm) < audio.RATE * 0.8 or not stt.ready():
                continue
            t = time.perf_counter()
            text = stt.transcribe_partial(pcm[-audio.RATE * 30:], prompt)
            # Slow passes (long audio) back off so the final result never waits long.
            interval = max(0.6, min(2.0, (time.perf_counter() - t) * 1.5))
            if text and text != shown and self.pressed_at == stamp and self.mode:
                shown = text
                notch.listening(mode, text, mic=mic)

    def _levels(self, stamp):
        """Mic level bars, ~15 times a second, so you can see the mic is hearing you."""
        while self.pressed_at == stamp and self.mode:
            notch.levels(self.rec.bars(5))
            time.sleep(0.066)

    def finish(self):
        mode, held = self.mode, (time.time() - self.pressed_at) * 1000
        self.mode = None
        self.pressed_at = 0  # stops the live transcript loop
        pcm = self.rec.stop()
        if self.other_key or held < S["min_hold_ms"]:
            log(f"{mode} hold ignored ({'shortcut' if self.other_key else f'{held:.0f} ms tap'})")
            notch.clear()
            return
        threading.Thread(target=self.process, args=(mode, pcm, dict(self.window)), daemon=True).start()

    # -- pipeline -----------------------------------------------------------------------
    def _transcribe(self, mode, pcm):
        """Whisper text for the recording, or None if recognition failed (the notch already says so)."""
        notch.transcribing(mode, "" if stt.ready() else "Loading the speech model (first use after a break)…")
        t0 = time.perf_counter()
        try:
            text = stt.transcribe(pcm, prompt=", ".join(S.get("vocabulary", [])))
        except Exception as e:
            log(f"stt failed: {e}")
            notch.error("Speech recognition failed.", mode)
            return None
        log(f"{mode}: heard {text!r} ({len(pcm) / 16000:.1f}s audio, stt {(time.perf_counter() - t0) * 1000:.0f} ms)")
        return text

    def _answer_question(self, text, asks):
        """A question is waiting in the notch (Gizmo's or Claude Code's): is this the answer? True if used."""
        res = answers.classify(text, asks)
        if not res:
            return False
        allow, choices, ask_id = res
        if not notch.answer(allow, choices, ask_id):
            return False
        log(f"answered question {ask_id} by voice: {text!r} -> {'allow' if allow else 'deny'} {choices}")
        notch.done(("Choice " + ", ".join(str(c + 1) for c in choices)) if choices else ("Allowed." if allow else "Denied."))
        return True

    def process(self, mode, pcm, window):
        if not audio.has_speech(pcm):
            notch.clear()
            return
        heard = None
        asks = notch.pending() if mode == "command" else []
        if asks:  # something is waiting for a yes/no: listen for the answer before anything else
            heard = self._transcribe(mode, pcm)
            if heard is None:
                return
            if self._answer_question(heard, asks):
                return
            if notch.asking.is_set():  # Gizmo's own question holds the command lock until it's answered
                notch.error("A question is waiting: say “yes” or “no”, or click Allow / Deny.", mode)
                return
        with self.busy:
            t0 = time.perf_counter()
            text = heard if heard is not None else self._transcribe(mode, pcm)
            if text is None:
                return
            if not text.strip() or _hallucination(text):
                notch.clear()
                return
            try:
                if mode == "dictate":
                    brain.handle_dictation(text, window)
                else:
                    brain.handle_command(text, window)
            except Exception as e:
                log(f"{mode} failed: {e!r}")
                notch.error(f"Something went wrong: {e}", mode)
            log(f"{mode} total {(time.perf_counter() - t0) * 1000:.0f} ms")


def _nectar_watch():
    """Nectar writes a new agent.json each start; when that happens, show any running timer again."""
    last = None
    while True:
        try:
            m = os.path.getmtime(notch.CFG)
        except OSError:
            m = None
        if m and last and m != last:
            time.sleep(4)  # let its windows load
            left = timers.current_countdown()
            if left:
                notch.timer("start", left)
                log(f"nectar restarted; re-sent timer ({left:.0f} s left)")
        last = m or last
        time.sleep(10)


def _hallucination(text):
    """Whisper's stock phrases on silence/noise."""
    t = text.strip().lower().strip(".! ")
    return t in {"thank you", "thanks for watching", "you", "bye", "thank you for watching",
                 "subtitles by the amara.org community", "please subscribe"}


def _single_instance():
    import win32api
    import win32event
    import winerror
    h = win32event.CreateMutex(None, False, "NectarVoiceSingleton")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        log("another Nectar Voice is already running; exiting")
        sys.exit(0)
    return h


def _read_wav(path):
    with wave.open(path) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        a = a.reshape(-1, ch).mean(1)
    if rate != 16000:
        a = np.interp(np.linspace(0, len(a), int(len(a) * 16000 / rate), endpoint=False),
                      np.arange(len(a)), a).astype(np.float32)
    return a


def main():
    try:  # real pixels everywhere, so UI Automation positions and the AI cursor line up on scaled displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--say")
    ap.add_argument("--dictate")
    ap.add_argument("--wav")
    ap.add_argument("--mode", default="command")
    a = ap.parse_args()
    if sys.stdout is None:  # pythonw: keep crash traces in the log folder
        import faulthandler
        sys.stderr = open(os.path.join(S["log_dir"], "voice-stderr.log"), "a", buffering=1, encoding="utf-8")
        faulthandler.enable(sys.stderr)
        faulthandler.dump_traceback_later(20, repeat=False, file=sys.stderr)
        log("starting (background)")

    apps.load_index()
    if a.say:
        print(brain.handle_command(a.say, inject.window_info()))
        return
    if a.dictate:
        out = textops.clean(textops.apply_aliases(a.dictate))
        print("clean:   ", out)
        if S["dictation_mode"] == "polished":
            print("polished:", textops.polish(out))
        return
    if a.wav:
        v = Voice()
        v.process(a.mode, _read_wav(a.wav), inject.window_info())
        return

    mutex = _single_instance()  # noqa: F841
    timers.load_saved()
    threading.Thread(target=_nectar_watch, daemon=True).start()
    v = Voice()
    import notifications
    notifications.start(lambda: v.mode is not None or v.busy.locked())
    import claude_usage
    claude_usage.start()
    keyboard.hook(v.on_key)
    log(f"Nectar Voice running: hold {S['command_key']} for commands, {S['dictate_key']} to dictate")
    try:
        keyboard.wait()
    finally:
        notch.clear()


if __name__ == "__main__":
    main()
