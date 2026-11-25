"""Every voice tool: what it does (for the AI), its arguments, its category, and the code.

The AI router only receives the categories a request needs, so adding tools doesn't make
prompts bigger. `confirm=True` tools ask Allow/Deny in the notch before running.
"""
import os
import re
import subprocess
import time
import urllib.parse
import webbrowser

import httpx

import apps
import inject
import llm
import system
import timers
from common import log

TOOLS = {}


def tool(name, category, description, params=None, required=(), confirm=False):
    def wrap(fn):
        TOOLS[name] = {"fn": fn, "category": category, "confirm": confirm, "schema": {
            "type": "function",
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object", "properties": params or {}, "required": list(required)}}}}
        return fn
    return wrap


S_ = lambda d, **kw: {"type": "string", "description": d, **kw}  # noqa: E731
N_ = lambda d, **kw: {"type": "number", "description": d, **kw}  # noqa: E731

# Context filled in by brain.py before tools run (target window captured at key press).
CTX = {"target": None}


# ---- apps & windows -----------------------------------------------------------------------
@tool("open_app", "apps", "Open (or switch to, if already running) an installed app by name.",
      {"name": S_("App name as the user said it")}, ["name"])
def open_app(name):
    msg = apps.focus_app(name) if apps.match_windows(name) else None
    if msg:
        return msg
    msg = apps.open_app(name)
    # Wait for its window so a following step ("…and write X in it") lands in the right place.
    for _ in range(24):
        wins = apps.match_windows(name)
        if wins:
            inject.focus(wins[0]["hwnd"])
            CTX["target"] = wins[0]["hwnd"]
            time.sleep(0.3)
            break
        time.sleep(0.25)
    return msg


@tool("close_app", "apps", "Close an app. scope 'one' closes only its front window, 'all' every window.",
      {"name": S_("App name"), "scope": S_("one or all", enum=["one", "all"])}, ["name"])
def close_app(name, scope="all"):
    return apps.close_app(name, scope)


@tool("close_all_apps", "apps", "Close every open app window, optionally keeping some.",
      {"except_apps": {"type": "array", "items": {"type": "string"}, "description": "Apps to keep open"}},
      confirm=True)
def close_all_apps(except_apps=()):
    return apps.close_all(except_apps or ())


@tool("list_open_apps", "apps", "Say which apps are open.")
def list_open_apps():
    names = apps.list_open()
    return "Open: " + ", ".join(names) + "." if names else "No apps are open."


@tool("window", "apps", "Act on the front window: minimize, maximize, restore, snap_left, snap_right, close; "
      "or show_desktop.", {"action": S_("Action", enum=["minimize", "maximize", "restore", "snap_left",
                                                         "snap_right", "close", "show_desktop"])}, ["action"])
def window(action):
    keys = {"minimize": "windows+down", "maximize": "windows+up", "restore": "windows+down",
            "snap_left": "windows+left", "snap_right": "windows+right", "close": "alt+f4", "show_desktop": "windows+d"}
    if CTX.get("target") and action != "show_desktop":
        inject.focus(CTX["target"])
        time.sleep(0.1)
    if action == "minimize":
        import win32gui, win32con
        win32gui.ShowWindow(inject.foreground(), win32con.SW_MINIMIZE)
    else:
        inject.press(keys[action])
    return "Done."


# ---- keys & typing ------------------------------------------------------------------------
@tool("press_keys", "keys", "Press a key or shortcut in the front app, e.g. 'f', '0', 'enter', 'ctrl+shift+t', "
      "'alt+tab', 'space', 'f11'. Use for 'press X' / 'click X on my keyboard'.",
      {"keys": S_("Key or combo, keyboard-library names joined with +"), "times": N_("Repeat count")}, ["keys"])
def press_keys(keys, times=1):
    if CTX.get("target"):
        inject.focus(CTX["target"])
        time.sleep(0.08)
    for _ in range(max(1, min(int(times), 50))):
        inject.press(keys.lower().replace("control", "ctrl").replace("escape", "esc"))
        time.sleep(0.05)
    return f"Pressed {keys}."


@tool("type_text", "writing", "Type/paste the given text exactly into the text box that has the cursor in the "
      "front app (e.g. text written in an earlier step).", {"text": S_("Text")}, ["text"])
def type_text(text):
    # Type where the cursor is now (an earlier step may have moved focus, e.g. opened a compose box).
    inject.paste_text(text)
    return "Typed."


# ---- media & sound ------------------------------------------------------------------------
@tool("media", "media", "Control what's playing: play_pause, next, previous, stop.",
      {"action": S_("Action", enum=["play_pause", "next", "previous", "stop"])}, ["action"])
def media(action):
    return system.media(action)


@tool("volume", "media", "Set the volume (level 0-100), change it (delta, e.g. +10 / -10), mute or unmute.",
      {"level": N_("Absolute volume 0-100"), "delta": N_("Relative change"),
       "mute": {"type": "boolean", "description": "true mutes, false unmutes"}})
def volume(level=None, delta=None, mute=None):
    if mute is not None:
        return system.mute(bool(mute))
    if level is not None:
        return system.set_volume(level)
    if delta is not None:
        return system.change_volume(delta)
    return f"Volume is {system.get_volume()}%."


# ---- system -------------------------------------------------------------------------------
@tool("brightness", "system", "Set screen brightness (level 0-100) or change it (delta).",
      {"level": N_("0-100"), "delta": N_("Relative change, e.g. 20 or -20")})
def brightness(level=None, delta=None):
    if level is None and delta is None:
        b = system.get_brightness()
        return f"Brightness is {b}%." if b is not None else "I can't read the brightness."
    return system.set_brightness(level, delta)


@tool("theme", "system", "Switch Windows to dark or light mode.", {"mode": S_("dark or light", enum=["dark", "light"])},
      ["mode"])
def theme(mode):
    return system.set_theme(mode)


@tool("keep_awake", "system", "Keep the PC awake (minutes, or none = until stopped), or stop keeping it awake.",
      {"minutes": N_("How long"), "stop": {"type": "boolean"}})
def keep_awake(minutes=None, stop=False):
    return system.stop_awake() if stop else system.keep_awake(minutes)


@tool("lock_pc", "system", "Lock the PC.")
def lock_pc():
    return system.lock()


@tool("power", "system", "Shut down, restart, sleep or sign out the PC; 'cancel' stops a pending shutdown.",
      {"action": S_("Action", enum=["shutdown", "restart", "sleep", "sign_out", "cancel"])}, ["action"], confirm=True)
def power(action):
    return system.power(action)


@tool("radio", "system", "Turn Wi-Fi or Bluetooth on or off.",
      {"kind": S_("wifi or bluetooth", enum=["wifi", "bluetooth"]), "on": {"type": "boolean"}}, ["kind", "on"])
def radio(kind, on):
    return system.radio(kind, on)


@tool("microphone", "system", "Which microphone voice listens with, list the microphones, or switch to one "
      "(name can be part of it, e.g. 'laptop', 'earphones', 'AB13X'; 'default' = the Windows default input).",
      {"action": S_("current, list or set", enum=["current", "list", "set"]), "name": S_("Mic to switch to")},
      ["action"])
def microphone(action, name=""):
    import audio
    from settings import save_setting
    if action == "list":
        mics = audio.list_mics()
        return "Microphones: " + "; ".join(audio.short_name(m) for m in mics) + "." if mics else "No microphones found."
    if action == "set":
        if not name or name.lower() in ("default", "windows default", "system default"):
            save_setting("mic", "")
            return f"Using the Windows default mic ({audio.short_name(audio.current_mic())})."
        words = {"laptop": "array", "laptop mic": "array", "built in": "array", "builtin": "array", "internal": "array",
                 "earphones": "usb", "earphone": "usb", "wired": "usb", "phone": "wo mic"}
        want = words.get(name.lower().strip(), name).lower()
        hit = next((m for m in audio.list_mics() if want in m.lower()), None)
        if not hit:
            return f"I couldn't find a mic matching {name}."
        save_setting("mic", hit[:28])  # raw device name: what recording matches on
        return f"Now listening with {audio.short_name(hit)}."
    return f"Listening with {audio.short_name(audio.current_mic())}."


@tool("battery", "system", "Battery level and charging state.")
def battery():
    return system.battery()


@tool("system_stats", "system", "CPU, RAM and disk usage.")
def system_stats():
    return system.stats()


@tool("open_settings", "system", "Open a Windows Settings page, e.g. display, sound, bluetooth, wifi, apps, "
      "battery, notifications, privacy, update.", {"page": S_("Page")}, ["page"])
def open_settings(page):
    pages = {"display": "display", "sound": "sound", "bluetooth": "bluetooth", "wifi": "network-wifi",
             "network": "network", "apps": "appsfeatures", "battery": "batterysaver", "power": "powersleep",
             "notifications": "notifications", "privacy": "privacy", "update": "windowsupdate",
             "storage": "storagesense", "personalization": "personalization", "mouse": "mousetouchpad",
             "keyboard": "typing", "time": "dateandtime", "default apps": "defaultapps", "camera": "privacy-webcam",
             "microphone": "privacy-microphone"}
    key = next((v for k, v in pages.items() if k in page.lower()), "")
    subprocess.Popen(["explorer.exe", f"ms-settings:{key}"])
    return f"Opened {page} settings."


# ---- timers -------------------------------------------------------------------------------
@tool("timer", "timers", "Start a countdown timer (shown in the notch clock with a progress ring).",
      {"seconds": N_("Length in seconds"), "label": S_("Optional name")}, ["seconds"])
def timer(seconds, label=""):
    import notch
    notch.timer("start", float(seconds))  # visible countdown in Nectar
    return timers.start_timer(float(seconds), label)  # our own ring + "Timer done" message


@tool("pause_timer", "timers", "Pause or resume the countdown timer.", {"pause": {"type": "boolean"}}, ["pause"])
def pause_timer(pause=True):
    import notch
    ok = notch.timer("pause" if pause else "resume")
    timers.pause_all(pause)
    return ("Paused." if pause else "Resumed.") if ok or timers.list_timers() else "No timer is running."


@tool("reminder", "timers", "Remind the user of something, either in N seconds or at a clock time (24 h 'HH:MM').",
      {"text": S_("What to remind"), "seconds": N_("In how many seconds"), "at": S_("Clock time HH:MM, 24-hour")},
      ["text"])
def reminder(text, seconds=None, at=None):
    if not seconds and not at:
        return "When should I remind you?"
    return timers.remind(text, float(seconds) if seconds else None, at)


@tool("cancel_timer", "timers", "Cancel timers/reminders: 'all', 'last', or words from its label.",
      {"which": S_("all, last, or label words")})
def cancel_timer(which="all"):
    import notch
    notch.timer("reset")
    return timers.cancel(which)


@tool("list_timers", "timers", "Say which timers and reminders are running.")
def list_timers():
    return timers.list_timers()


@tool("stopwatch", "timers", "Stopwatch: start, stop, or status.",
      {"action": S_("start, stop or status", enum=["start", "stop", "status"])}, ["action"])
def stopwatch(action):
    import notch
    if action in ("start", "stop"):
        notch.stopwatch(action)
    return timers.stopwatch(action)


# ---- web ----------------------------------------------------------------------------------
SEARCH = {"google": "https://www.google.com/search?q={}", "youtube": "https://www.youtube.com/results?search_query={}",
          "bing": "https://www.bing.com/search?q={}", "duckduckgo": "https://duckduckgo.com/?q={}",
          "wikipedia": "https://en.wikipedia.org/w/index.php?search={}", "amazon": "https://www.amazon.in/s?k={}",
          "github": "https://github.com/search?q={}", "maps": "https://www.google.com/maps/search/{}",
          "images": "https://www.google.com/search?tbm=isch&q={}", "spotify": "https://open.spotify.com/search/{}",
          "reddit": "https://www.reddit.com/search/?q={}"}


@tool("open_url", "web", "Open a website or URL in the default browser (Brave). Bare names like 'youtube' are fine.",
      {"url": S_("URL or site name")}, ["url"])
def open_url(url):
    u = url.strip()
    if not re.match(r"https?://", u):
        u = u.replace(" ", "")
        u = "https://" + (u if "." in u else u + ".com")
    webbrowser.open(u)
    return f"Opened {re.sub(r'^https?://(www\.)?', '', u).rstrip('/')}."


@tool("web_search", "web", "Search the web or a specific site (google, youtube, wikipedia, amazon, github, maps, "
      "images, spotify, reddit) and open the results. Leave site empty when the user didn't name one: the site "
      "they're on (e.g. YouTube) is used, else Google.",
      {"query": S_("Search words only"), "site": S_("Where to search", enum=list(SEARCH))}, ["query"])
def web_search(query, site=None):
    if not site:
        title = (CTX.get("window") or {}).get("title", "").lower()
        site = next((s for s in ("youtube", "amazon", "github", "reddit", "wikipedia", "spotify") if s in title),
                    "google")
    webbrowser.open(SEARCH.get(site, SEARCH["google"]).format(urllib.parse.quote_plus(query)))
    return f"Searching {site.capitalize()} for {query}."


TAB_KEYS = {"new": "ctrl+t", "close": "ctrl+w", "reopen": "ctrl+shift+t", "next": "ctrl+tab",
            "previous": "ctrl+shift+tab", "last": "ctrl+9", "private": "ctrl+shift+n", "new_window": "ctrl+n"}


@tool("browser_tab", "web", "Browser tabs in the browser in front: new, close, reopen, next, previous, last, "
      "private (incognito window), new_window, number (go to tab N), or find (switch to an already-open tab by its "
      "title or site, e.g. 'Gmail').",
      {"action": S_("What to do", enum=list(TAB_KEYS) + ["number", "find"]), "n": N_("Tab number for 'number'"),
       "name": S_("Tab title/site for 'find'")}, ["action"])
def browser_tab(action, n=None, name=None):
    if CTX.get("target"):
        inject.focus(CTX["target"])
        time.sleep(0.08)
    if action == "number" and n:
        inject.press(f"ctrl+{max(1, min(int(n), 9))}")
        return f"Tab {int(n)}."
    if action == "find" and name:
        # Brave/Chrome/Edge tab search: Ctrl+Shift+A, type, Enter.
        inject.press("ctrl+shift+a")
        time.sleep(0.45)
        inject.paste_text(name, restore=True)
        time.sleep(0.5)
        inject.press("enter")
        return f"Switched to the {name} tab."
    keys = TAB_KEYS.get(action)
    if not keys:
        return f"(unknown tab action {action})"
    inject.press(keys)
    return {"new": "New tab.", "close": "Closed the tab.", "reopen": "Reopened the last tab."}.get(action, "Done.")


@tool("fullscreen", "web", "Make the video or window in front fullscreen (YouTube uses F, other apps F11).")
def fullscreen():
    title = (CTX.get("window") or {}).get("title", "").lower()
    if CTX.get("target"):
        inject.focus(CTX["target"])
        time.sleep(0.1)
    inject.press("f" if any(s in title for s in ("youtube", "netflix", "prime video", "hotstar")) else "f11")
    return "Fullscreen."


@tool("youtube_play", "web", "Find a video on YouTube and play it (first result unless n is given), optionally "
      "fullscreen.", {"query": S_("What to search for"), "n": N_("Which result, 1 = first"),
                      "fullscreen": {"type": "boolean"}}, ["query"])
def youtube_play(query, n=1, fullscreen=False):
    try:
        html = httpx.get("https://www.youtube.com/results", params={"search_query": query},
                         headers={"Accept-Language": "en", "User-Agent": "Mozilla/5.0"}, timeout=8).text
        ids = list(dict.fromkeys(re.findall(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html)))
        titles = re.findall(r'"videoRenderer":\{"videoId":"[\w-]{11}".*?"title":\{"runs":\[\{"text":"(.*?)"\}', html)
    except httpx.HTTPError as e:
        log(f"youtube search failed: {e}")
        ids, titles = [], []
    if not ids:
        return web_search(query, "youtube")
    i = max(0, min(int(n or 1) - 1, len(ids) - 1))
    webbrowser.open(f"https://www.youtube.com/watch?v={ids[i]}")
    if fullscreen:
        time.sleep(4.5)
        inject.press("f")
    title = titles[i] if i < len(titles) else query
    return f"Playing \"{title}\"."


# ---- writing ------------------------------------------------------------------------------
WRITE_SYSTEM = ("You write text for the user to send or use. Write exactly what they ask for, in their tone; "
                "match the requested length and formality. No preamble, no quotes, no notes; output only the text. "
                "Use [placeholders] only for details you truly don't know.")


@tool("write", "writing", "Create any text the user asks for (prompt, email, message, letter, caption, list, "
      "code, answer to paste...) from an instruction. It goes into the text box the cursor is in; if the user "
      "says clipboard, or no text box has the cursor, it goes to the clipboard instead. The result includes the "
      "text, so a later step can paste it somewhere else (type_text).",
      {"instruction": S_("What to write, with every detail the user gave"),
       "destination": S_("auto (default: text box if one is focused, else clipboard) or clipboard",
                         enum=["auto", "clipboard"])}, ["instruction"])
def write(instruction, destination="auto"):
    import screen
    # Your rule: clipboard only when you said so; otherwise the box with the cursor.
    if destination == "clipboard" and not re.search(r"clipboard|\bcopy\b|\bcopied\b", CTX.get("utterance", ""), re.I):
        destination = "auto"
    out = llm.text(WRITE_SYSTEM, instruction, temperature=0.6, max_tokens=1500)
    if not out:
        return "(the writing model returned nothing)"
    preview = out if len(out) <= 400 else out[:400] + "…"
    where = None
    if destination != "clipboard" and CTX.get("target"):
        inject.focus(CTX["target"])
        time.sleep(0.12)
        box = screen.focused_box()
        if box["editable"] is not False:  # a text box, or can't tell (custom editors): type it
            if box["selected"].strip():
                inject.press("right")  # don't overwrite what's selected: write after it
                time.sleep(0.05)
                out = "\n\n" + out
            inject.paste_text(out)
            where = box["name"] or "the text box"
    if where:
        return f"Typed into {where}. Text: {preview}"
    inject.set_clipboard(out)
    reason = "" if destination == "clipboard" else " (no text box had the cursor)"
    return f"Copied to clipboard{reason}. Text: {preview}"


REWRITE_SYSTEM = ("Rewrite the user's text as their instruction says. Keep facts, names, numbers and links unless "
                  "told otherwise; keep the original formatting style unless asked to change it. Output only the "
                  "rewritten text: no preamble, no quotes.")


def rewrite_selection(instruction, selected):
    out = llm.text(REWRITE_SYSTEM, f"Instruction: {instruction}\n\nText:\n{selected}", temperature=0.5,
                   max_tokens=max(600, len(selected)))
    if CTX.get("target"):
        inject.focus(CTX["target"])
        time.sleep(0.08)
    inject.paste_text(out)
    return "Rewritten."


# ---- screen (phase 2) ---------------------------------------------------------------------
def _target():
    return CTX.get("target") or inject.foreground()


@tool("screen_read", "screen", "Look at the user's screen and answer a question about what's visible: 'what's on "
      "my screen', 'what does this error say', 'summarize this page', 'what is this'.",
      {"question": S_("The user's question, in their words")}, ["question"])
def screen_read(question):
    import screen
    img, _ = screen.capture(_target())
    return screen.ask_vision(question, img) or "I couldn't make out the screen."


@tool("screen_copy", "screen", "Copy specific text that is visible on screen (or in an image on screen) to the "
      "clipboard, e.g. 'the password in row 3', 'the text in this image', 'the error message', 'the OTP'.",
      {"what": S_("Which text to copy, as the user described it")}, ["what"])
def screen_copy(what):
    import screen
    img, _ = screen.capture(_target(), max_dim=2000)
    text = screen.ask_vision(
        f"Find this on the screen and return it exactly: {what}", img,
        system="Return ONLY the exact characters of the requested text from the screenshot, nothing else: no "
               "quotes, labels or explanation. Keep line breaks if it spans lines. If it isn't visible, reply "
               "exactly: NOT_FOUND", max_tokens=800).strip()
    if not text or text == "NOT_FOUND":
        return f"I couldn't find {what} on the screen."
    inject.set_clipboard(text)
    preview = text if len(text) <= 40 else text[:40] + "…"
    return f"Copied {what}: \"{preview}\""


@tool("screen_click", "screen", "Press a button, link, tab, menu item or checkbox in the window in front, by its "
      "name or description: 'click sign in', 'tick remember me', 'open the settings tab', 'click the first result'.",
      {"target": S_("The control, as the user described it"),
       "kind": S_("Optional: checkbox, link, tab", enum=["checkbox", "link", "tab"])}, ["target"])
def screen_click(target, kind=None):
    import screen
    hwnd = _target()

    def run():
        els = screen.elements(hwnd)
        el = screen.find(target, els, kind, goal=f"click {target}")
        if not el:
            return None, f"I couldn't find \"{target}\" in this window."
        if screen.DANGER.search(el["name"]):
            return el, "confirm"
        screen.press(el)
        return el, f"Clicked \"{el['name'][:60]}\"."

    el, msg = screen.with_uia(run)
    if msg == "confirm":
        import notch
        if notch.ask(f"Click \"{el['name'][:50]}\"?", "This button may buy, send or delete something.") is not True:
            return "Not clicked."
        screen.with_uia(lambda: screen.press(el))
        return f"Clicked \"{el['name'][:60]}\"."
    return msg


@tool("screen_fill", "screen", "Type into a text box in the window in front, found by its label: 'fill in my name "
      "as Rudra', 'type hello in the search box', 'put the cursor in the email field' (text empty = just focus).",
      {"field": S_("The box's label or description"), "text": S_("What to type (empty to just focus it)")},
      ["field"])
def screen_fill(field, text=""):
    import screen
    hwnd = _target()

    def run():
        el = screen.find(field, screen.elements(hwnd), "field", goal=f"the {field} text box")
        if not el:
            return None
        try:
            el["ctrl"].SetFocus()
        except Exception:
            screen.press(el)
        return el

    el = screen.with_uia(run)
    if not el:
        return f"I couldn't find a \"{field}\" box here."
    if text:
        time.sleep(0.1)
        inject.press("ctrl+a")
        inject.paste_text(text)
        return f"Typed into {el['name'][:40]}."
    return f"Cursor is in {el['name'][:40]}."


@tool("youtube_click", "web", "On a YouTube page that's open (home or search results), play the n-th video shown, "
      "optionally fullscreen. Use for 'click the first video' / 'play the second one' when YouTube is in front.",
      {"n": N_("1 = first video"), "fullscreen": {"type": "boolean"}})
def youtube_click(n=1, fullscreen=False):
    import screen
    hwnd = _target()
    n = max(1, int(n or 1))

    def run():
        vids = [e for e in screen.elements(hwnd, limit=600, budget_s=4)
                if e["type"] in ("Hyperlink", "Text") and re.search(
                    r"\b\d+ (?:hours?|minutes?|seconds?)\b|\b\d+:\d{2}\b", e["name"]) and len(e["name"]) > 15]
        seen, uniq = set(), []
        for v in vids:
            key = v["name"][:50]
            if key not in seen:
                seen.add(key)
                uniq.append(v)
        if len(uniq) >= n:
            screen.press(uniq[n - 1])
            return uniq[n - 1]["name"]
        return None

    title = screen.with_uia(run)
    if not title:
        # Fall back to the search words in the address bar.
        url = screen.browser_url(hwnd)
        m = re.search(r"search_query=([^&]+)", url)
        if not m:
            return "I couldn't find videos on this page."
        return youtube_play(urllib.parse.unquote_plus(m.group(1)), n, fullscreen)
    if fullscreen:
        time.sleep(4)
        inject.press("f")
    short = re.split(r" by | \d+ (?:hours?|minutes?)", title)[0][:70]
    return f"Playing \"{short}\"."


@tool("screenshot", "screen", "Take a screenshot of the screen and save it (Pictures\\Screenshots); "
      "snip=true opens the snipping tool instead.", {"snip": {"type": "boolean"}})
def screenshot(snip=False):
    if snip:
        inject.press("windows+shift+s")
        return "Snip away."
    import screen
    _, full = screen.capture(_target())
    path = os.path.join(screen.pictures_dir("Screenshots"), time.strftime("Screenshot %Y-%m-%d %H%M%S.png"))
    full.save(path)
    return f"Saved screenshot to Pictures\\Screenshots."


@tool("camera_photo", "screen", "Take a photo with the webcam (opens the Camera app and presses the shutter). "
      "Not for screenshots.")
def camera_photo():
    import apps as _apps
    subprocess.Popen(["explorer.exe", "microsoft.windows.camera:"])
    for _ in range(40):
        time.sleep(0.25)
        wins = [w for w in _apps.windows() if w["title"].lower() == "camera"]
        if wins:
            break
    else:
        return "The Camera app didn't open."
    inject.focus(wins[0]["hwnd"])
    time.sleep(2.2)  # let the camera start and focus
    inject.press("space")
    return "Photo taken: it's in Pictures\\Camera Roll."


def schemas(categories=None):
    return [t["schema"] for t in TOOLS.values() if categories is None or t["category"] in categories]
