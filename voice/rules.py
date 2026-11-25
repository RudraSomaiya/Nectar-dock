"""Instant command rules: no AI, no network. A rule fires only when it's sure;
anything else goes to the AI router. Returns (tool_name, args) or None."""
import re

import apps

LEAD = re.compile(r"^(?:(?:hey|hi|ok|okay)\s+(?:nixi|nectar|nexi)|nixi|nectar|please|can you|could you|"
                  r"would you|just|now|and)[,\s]+", re.I)
SITES = {"youtube", "gmail", "google", "github", "netflix", "instagram", "twitter", "x", "reddit", "linkedin",
         "chatgpt", "amazon", "flipkart", "wikipedia", "whatsapp web", "google drive", "drive", "google maps",
         "maps", "prime video", "hotstar", "spotify web", "groq console"}
SITE_URLS = {"gmail": "https://mail.google.com", "google drive": "https://drive.google.com",
             "drive": "https://drive.google.com", "google maps": "https://maps.google.com",
             "maps": "https://maps.google.com", "whatsapp web": "https://web.whatsapp.com", "x": "https://x.com",
             "chatgpt": "https://chatgpt.com", "prime video": "https://www.primevideo.com",
             "hotstar": "https://www.hotstar.com", "groq console": "https://console.groq.com",
             "amazon": "https://www.amazon.in", "flipkart": "https://www.flipkart.com"}
NUM_WORDS = {"zero": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
             "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "half an": 0.5, "half a": 0.5}
UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600}
COMPOUND = re.compile(r"\b(and then|then|and|after that|also)\b|,")
BROWSER_KEYS = [
    (r"(open |create |make )?(a )?new (browser )?tab|open (a )?tab", "ctrl+t"),
    (r"(close|shut) (this |the |current |that )?s?tab", "ctrl+w"),
    (r"(re-?open|restore|bring back) (the |my )?(last |closed |last closed )?tab", "ctrl+shift+t"),
    (r"(go to |switch to )?(the )?next tab", "ctrl+tab"),
    (r"(go to |switch to )?(the )?previous tab", "ctrl+shift+tab"),
    (r"(reload|refresh)( the| this)?( page| tab)?", "f5"),
    (r"go back( a page)?|(go to )?(the )?previous page|back", "alt+left"),
    (r"go forward( a page)?|(go to )?(the )?next page", "alt+right"),
    (r"(open |create )?(a )?new (browser )?window", "ctrl+n"),
    (r"(open |create )?(a |an )?(new )?(incognito|private) (window|tab)", "ctrl+shift+n"),
    (r"scroll down", "page down"),
    (r"scroll up", "page up"),
    (r"(go to |scroll to )?(the )?top( of the page)?", "home"),
    (r"(go to |scroll to )?(the )?bottom( of the page)?", "end"),
    (r"zoom in", "ctrl+plus"),
    (r"zoom out", "ctrl+minus"),
    (r"(select all|copy this|copy that|copy it)", None),
]
BROWSER_KEYS = [(p, k) for p, k in BROWSER_KEYS if k]
# Things that are never app names: "open a new tab" must not fuzzy-match an installed app.
NOT_APPS = re.compile(r"\b(tab|tabs|window|windows|page|folder|file|link|video|song|playlist|chat|email|"
                      r"message|settings? (page|for)|website|site)\b")


def normalize(text):
    t = text.strip().lower()
    t = re.sub(r"[.!?]+$", "", t).strip()
    prev = None
    while prev != t:
        prev, t = t, LEAD.sub("", t).strip()
    return t


def _num(s):
    s = s.strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    return NUM_WORDS.get(s)


def _duration(t):
    m = re.search(r"(\d+(?:\.\d+)?|[a-z]+(?: [a-z]+)?)[ -](second|sec|minute|min|hour|hr)s?\b", t)
    if not m:
        return None
    n = _num(m.group(1)) or _num(m.group(1).split()[-1])
    return n * UNITS[m.group(2)] if n else None


def match(text):
    t = normalize(text)
    if not t:
        return None
    single = not COMPOUND.search(t)

    # --- media ---
    if re.fullmatch(r"(next|skip)( song| track| video)?|play (the )?next( song| track)?", t):
        return "media", {"action": "next"}
    if re.fullmatch(r"(previous|last)( song| track)|previous|play (the )?previous( song| track)?", t):
        return "media", {"action": "previous"}
    if re.fullmatch(r"(pause|resume|play|stop)( the)?( music| song| video| media| it)?", t):
        return "media", {"action": "stop" if t.startswith("stop") else "play_pause"}

    # --- volume ---
    if re.fullmatch(r"(mute|unmute)( the)?( volume| sound| audio| it)?", t):
        return "volume", {"mute": t.startswith("mute")}
    m = re.fullmatch(r"(?:set |change |turn )?(?:the )?volume (?:to |at )?(\d+)(?: ?%| percent)?", t)
    if m:
        return "volume", {"level": int(m.group(1))}
    m = re.fullmatch(r"(?:turn |increase |raise |decrease |lower |reduce )?(?:the )?volume (up|down)?"
                     r"(?: by (\d+)(?: ?%| percent)?)?|(turn it|volume) (up|down)", t)
    if m and (m.group(1) or m.group(4) or re.match(r"(increase|raise|decrease|lower|reduce)", t)):
        up = (m.group(1) or m.group(4)) == "up" or re.match(r"(increase|raise)", t)
        step = int(m.group(2)) if m.group(2) else 10
        return "volume", {"delta": step if up else -step}

    # --- brightness ---
    if "brightness" in t and single:
        if re.search(r"\b(max|maximum|full|100)\b", t):
            return "brightness", {"level": 100}
        if re.search(r"\b(min|minimum|lowest)\b", t):
            return "brightness", {"level": 0}
        m = re.search(r"(\d+)", t)
        if m and re.search(r"\b(to|at|set)\b", t):
            return "brightness", {"level": int(m.group(1))}
        if re.search(r"\b(up|increase|raise|higher|brighter)\b", t):
            return "brightness", {"delta": int(m.group(1)) if m else 20}
        if re.search(r"\b(down|decrease|lower|reduce|dim)\b", t):
            return "brightness", {"delta": -(int(m.group(1)) if m else 20)}

    # --- system ---
    if re.fullmatch(r"lock (my |the )?(pc|laptop|computer|screen|system)", t):
        return "lock_pc", {}
    if re.fullmatch(r"(turn on |switch to |enable )?(dark|light) (mode|theme)( on)?", t):
        return "theme", {"mode": "dark" if "dark" in t else "light"}
    if re.fullmatch(r"(how much |what'?s (my |the )?)?battery( level| percentage| status)?( left)?", t):
        return "battery", {}
    if re.fullmatch(r"(show( me)?|go to) (the )?desktop|minimi[sz]e (everything|all( windows)?)", t):
        return "window", {"action": "show_desktop"}
    m = re.fullmatch(r"(minimi[sz]e|maximi[sz]e|restore)( this| the)?( window| app)?", t)
    if m:
        return "window", {"action": m.group(1).replace("ise", "ize")}

    # --- keys ---
    m = re.fullmatch(r"(?:press|hit|click|tap)(?: on)?(?: the)? (.+?)(?: key| button)?(?: on (?:my |the )?keyboard)?", t)
    if m and ("keyboard" in t or t.startswith(("press", "hit"))) and (
            len(m.group(1).split()) <= 3 or " plus " in m.group(1)):
        k = m.group(1).replace(" plus ", "+").replace("control", "ctrl").replace(" ", "+")
        k = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
             "seven": "7", "eight": "8", "nine": "9", "escape": "esc", "return": "enter"}.get(k, k)
        return "press_keys", {"keys": k}

    # --- browser / tabs (keys go to the window you were in) ---
    for pat, keys in BROWSER_KEYS:
        if re.fullmatch(pat, t):
            return "press_keys", {"keys": keys}
    if re.fullmatch(r"(go |make it |switch to |put it in |enter |exit |leave |close |get out of |go out of |"
                    r"come out of )?(the )?(full ?screen|full screen mode)( mode)?|(maximi[sz]e|fullscreen) (the )?video", t):
        return "fullscreen", {}  # toggles: F on YouTube, F11 elsewhere
    m = re.fullmatch(r"(?:open|go to|switch to|show)( the)? tab (?:number )?(\d|one|two|three|four|five|six|seven|"
                     r"eight|nine)|(?:open|go to|switch to)( the)? (first|second|third|fourth|fifth|last) tab", t)
    if m:
        word = m.group(2) or m.group(4)
        n = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": 9}.get(word) or int(_num(word))
        return "browser_tab", {"action": "number", "n": n}

    # --- timers ---
    if re.search(r"\btimers?\b", t) and single:
        if re.search(r"\b(cancel|stop|delete|remove|clear|end)\b", t):
            return "cancel_timer", {"which": "all" if re.search(r"\b(all|timers)\b", t) else "last"}
        if re.search(r"\b(pause|hold)\b", t):
            return "pause_timer", {"pause": True}
        if re.search(r"\b(resume|continue|unpause)\b", t):
            return "pause_timer", {"pause": False}
        d = _duration(t)
        if d and re.search(r"\b(start|set|put|create|make|begin|add|timer for|new)\b", t):
            return "timer", {"seconds": d}
    if re.fullmatch(r"(start|stop)( the| a)? stopwatch", t):
        return "stopwatch", {"action": t.split()[0]}

    # --- open / close (single target only) ---
    if not single:
        return None
    m = re.fullmatch(r"(?:open|launch|start|run|switch to|go to|bring up)\s+(?:the |my )?(.+?)(?: app| application)?", t)
    if m:
        target = m.group(1).strip()
        if target in SITES:
            return "open_url", {"url": SITE_URLS.get(target, target)}
        if NOT_APPS.search(target):
            return None
        if apps.find_app(target, min_score=86) or apps.match_windows(target):
            return "open_app", {"name": target}
        return None
    m = re.fullmatch(r"(?:close|quit|exit|kill)\s+(?:the |my )?(.+?)(?: app| application| window)?", t)
    if m and not re.search(r"\b(all|every|everything)\b", m.group(1)):
        target = m.group(1)
        scope = "one" if re.search(r"\b(one|a|this) ", target + " ") else "all"
        target = re.sub(r"^(one of the |one |a |this |that )", "", target)
        target = re.sub(r"^(instances?|windows?|copy|copies) of (the )?", "", target)
        target = re.sub(r" (instances?|windows?|tabs?)$", "", target)
        return "close_app", {"name": target, "scope": scope}
    return None
