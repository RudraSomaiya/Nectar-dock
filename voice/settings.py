"""Voice settings: defaults, overridden by config/voice.json (created on first run)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(HERE, "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "voice.json")

DEFAULTS = {
    # Reused from the existing install: nothing is downloaded again.
    "whisper_model": r"D:\Programs\heynixi\models\whisper\faster-whisper-large-v3-turbo",
    "cuda_dir": r"D:\Programs\heynixi\user-runtime\cuda",
    "language": "en",
    "beam_size": 1,
    # Seconds without a request before the speech worker quits and frees ~700 MB RAM
    # and ~1.6 GB GPU memory. 0 keeps it loaded.
    "stt_idle_exit_s": 600,
    # When the worker is still loading as you finish speaking: "wait" (stay local)
    # or "groq" (send that one clip to Groq's Whisper for a faster answer).
    "cold_start": "wait",
    "command_key": "right ctrl",
    "dictate_key": "right alt",
    # Microphone name (part of it is enough); "" uses the Windows default input.
    "mic": "",
    # Dictation: "clean" (rules only, instant) or "polished" (Groq tidies grammar/punctuation).
    "dictation_mode": "polished",
    # Ignore key holds shorter than this (a tap, or the key used in a shortcut).
    "min_hold_ms": 300,
    # Your words: what Whisper hears -> what you meant. Also used for app names.
    "aliases": {"brief": "brave", "breve": "brave", "cloud": "claude", "whisper flow": "wispr flow", "whisperflow": "wispr flow",
                "whisper blue": "wispr flow", "spare flow": "wispr flow", "valo": "valorant"},
    # Words Whisper should spell your way (names, jargon).
    "vocabulary": ["Groq", "Nectar", "Claude", "Wispr Flow", "Brave", "Valorant"],
    # Spoken snippets: say "insert <name>" while dictating.
    "snippets": {},
    "groq_models": ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"],
    "vision_model": "qwen/qwen3.8-27b",
    "data_dir": os.path.join(HERE, "data"),
    "log_dir": os.path.join(HERE, "logs"),
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            s.update(json.load(f))
    except FileNotFoundError:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in DEFAULTS.items() if k not in ("data_dir", "log_dir")}, f, indent=2)
    except (OSError, ValueError):
        pass
    return s


def save_setting(key, value):
    """Change one setting in config/voice.json and in the running settings."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data[key] = value
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    from common import S
    S[key] = value


def groq_key():
    with open(os.path.join(CONFIG_DIR, "groq.key"), encoding="utf-8") as f:
        return f.read().strip()
