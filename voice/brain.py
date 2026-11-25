"""What happens after you speak: dictation goes into the box you're in; commands go through
selection rewrite -> instant rules -> the AI router with tools."""
import datetime as dt
import json
import re
import time

import inject
import llm
import notch
import rules
import textops
import tools
from common import S, log

EDIT_RE = re.compile(
    r"^(?:(?:can you|could you|please|now)\s+)*(?:make|rewrite|re-write|rephrase|reword|change|convert|turn|expand|"
    r"lengthen|shorten|condense|simplify|summari[sz]e|fix|correct|proofread|polish|improve|translate|format|"
    r"replace|remove|add|put|clean up|tidy up|tighten)\b", re.I)

# Word starts that pull in a tool group (prefix match, so plurals and -ing forms count).
CATEGORY_WORDS = {
    "apps": r"\b(open|close|quit|launch|start|switch|exit|kill|app|window|minimi|maximi|snap|desktop)",
    "keys": r"\b(press|type|key|keyboard|shortcut|hit |tap )",
    "media": r"\b(play|pause|resume|next|previous|song|track|music|volume|mute|unmute|louder|quieter|sound)",
    "system": r"\b(bright|dim|dark|light mode|theme|awake|sleep|lock|shut ?down|restart|reboot|sign out|"
              r"log ?out|wi-?fi|bluetooth|batter|cpu|ram\b|memory|disk|setting|stats|mic\b|microphone|headset|"
              r"earphone)",
    "timers": r"\b(timer|remind|alarm|stopwatch|minute|second|hour|cancel)",
    "web": r"\b(search|google|youtube|video|website|site|url|browser|brave|chrome|look up|wikipedia|amazon|"
           r"github|maps|reddit|tab)",
    "writing": r"\b(write|draft|compose|email|mail|message|reply|letter|essay|paragraph|note|caption|tweet|post)",
    "files": r"\b(file|pdf|doc|document|docx|ppt|slides?|presentation|excel|sheet|spreadsheet|dropped|attached|"
             r"this (one|report|paper|notes?|assignment)|summar|it say)",
    "screen": r"\b(screen|click|tick|untick|check ?box|select|choose|fill|field|box|button|link|copy|error|"
              r"what('?s| is) (this|that)|camera|photo|selfie|picture|screenshot|snip|page|image|read|"
              r"cursor|type in|enter my|first|second|third|video)",
}

ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
NTH_VIDEO = re.compile(r"(?:click|play|open|start|watch)(?: on)?(?: the)? (\w+)(?: one| video| result)"
                       r"(?: video)?(?: on (?:my |the )?screen)?(?: and (?:make it |go |put it )?(full ?screen))?$", re.I)

ROUTER_SYSTEM = """You are Gizmo, a voice assistant on the user's Windows laptop. The user spoke a command;
speech recognition may have small mistakes (e.g. "brief" = Brave, "cloud" = Claude), so read for intent.
Use the tools to act. For several steps, call the tools in order (you may call more than one, or call again
after seeing results). If the user only asks a question, answer it directly in one or two short sentences
(it shows in a small notch).
Whenever the user wants text created (a prompt, email, message, letter, caption, list, code, anything to use
or paste), call `write`: never put the created text in your reply. It types into the box with the cursor, or
the clipboard if they asked for that. To put text somewhere after other steps (e.g. into a compose box you
just opened), use the text from the write result with type_text once the cursor is there.
Your final reply may only state what the tool results show actually happened; if a step failed or wasn't
done, say so. Never claim you did something a tool didn't do. If a request is unclear or
needs something no tool can do, say so briefly. Reply in the language the user used; for Hindi or
Hinglish, reply in romanized Hinglish (Latin letters), never Devanagari.
Prefer one direct tool over many small ones (e.g. youtube_play instead of open browser + search + click).
Things in the window in front (buttons, links, boxes, text, errors, images) use the screen_* tools;
"press X on my keyboard" is press_keys; a YouTube page in front + "click/play the Nth video" is youtube_click.
When independent steps are needed, call them all in one response. After tool results come back, either
call the next step or reply with one short sentence saying what happened (no tool output repeated verbatim
unless it answers a question)."""


def _now():
    return dt.datetime.now().strftime("%A %d %B %Y, %I:%M %p")


def _categories(text):
    cats = {c for c, rx in CATEGORY_WORDS.items() if re.search(rx, text, re.I)}
    return cats or None  # None = all tools


def run_tool(name, args):
    t = tools.TOOLS.get(name)
    if not t:
        return f"(unknown tool {name})"
    if t["confirm"]:
        detail = ", ".join(f"{k}: {v}" for k, v in args.items()) or name
        ok = notch.ask(name.replace("_", " ").capitalize() + "?", detail)
        if ok is not True:
            return "Cancelled (not confirmed)."
    try:
        log(f"tool {name} {json.dumps(args)[:300]}")
        out = str(t["fn"](**args))
        # Steps that open or switch things move the cursor: later steps should follow it.
        if t["category"] in ("apps", "web", "screen") and name not in ("screen_read", "screen_copy", "screenshot"):
            time.sleep(0.15)
            fg = inject.foreground()
            if fg:
                tools.CTX["target"] = fg
                tools.CTX["window"] = inject.window_info(fg)
        return out
    except TypeError as e:
        return f"(bad arguments for {name}: {e})"
    except Exception as e:
        log(f"tool {name} failed: {e}")
        return f"(failed: {e})"


def route_with_ai(text, window):
    cats = _categories(text)
    if cats is not None:
        cats |= {"writing"}  # creating text is always possible, whatever words were used
    schemas = tools.schemas(cats)
    ctx = f"Now: {_now()}. Front window: {window.get('title') or '?'} ({window.get('exe') or '?'})."
    messages = [{"role": "system", "content": ROUTER_SYSTEM + "\n" + ctx}, {"role": "user", "content": text}]
    results = []
    for _ in range(6):
        msg = llm.chat(messages, tools=schemas, temperature=0.2, max_tokens=700, models=S.get("router_models"))
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip() or (" ".join(results) if results else "Done.")
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            if cats is not None and name not in {s["function"]["name"] for s in schemas}:
                out = "(tool not available)"
            else:
                out = run_tool(name, llm.parse_args(call))
            results.append(out)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
        # Loop: the model either calls the next step or replies with a short summary.
    return " ".join(results) or "Done."


REFERS_TO_SELECTION = re.compile(r"\b(this|that|it|these|selected|selection|highlighted|the text|this text|"
                                 r"paragraph|sentence|line)\b", re.I)


def handle_command(text, window):
    t0 = time.perf_counter()
    text = textops.apply_aliases(text)
    notch.thinking(text)
    tools.CTX["target"] = window.get("hwnd")
    tools.CTX["window"] = window
    tools.CTX["utterance"] = text

    # 1. "make this longer" with text selected: rewrite the selection, any wording.
    norm = rules.normalize(text)
    about_file = re.search(CATEGORY_WORDS["files"], norm) and not re.search(r"\b(text|selected|selection|this "
                                                                             r"(paragraph|sentence|line))\b", norm)
    in_explorer = (window.get("exe") or "").lower() == "explorer.exe"
    if EDIT_RE.search(norm) and not about_file and not in_explorer:
        selected = inject.get_selection(window.get("hwnd"))
        log(f"edit request, selection {len(selected)} chars")
        if selected.strip():
            notch.thinking(f"{text} ({len(selected.split())} words selected)")
            reply = tools.rewrite_selection(text, selected)
            log(f"rewrite -> {reply} ({(time.perf_counter() - t0) * 1000:.0f} ms)")
            notch.done(reply)
            return reply
        if REFERS_TO_SELECTION.search(norm):
            reply = "I couldn't see any selected text. Select it first, then hold Right Ctrl and ask again."
            notch.error(reply)
            return reply

    # 2. Instant rules. "Click the first video" on YouTube goes straight to the video list.
    m = NTH_VIDEO.search(norm)
    if m and m.group(1) in ORDINALS and "youtube" in (window.get("title") or "").lower():
        hit = ("youtube_click", {"n": ORDINALS[m.group(1)], "fullscreen": bool(m.group(2))})
    else:
        hit = rules.match(text)
    if hit:
        name, args = hit
        reply = run_tool(name, args)
        log(f"rule {name} -> {reply} ({(time.perf_counter() - t0) * 1000:.0f} ms)")
        notch.done(reply)
        return reply

    # 3. AI router.
    try:
        reply = route_with_ai(text, window)
    except Exception as e:
        log(f"router failed: {e}")
        notch.error("Groq didn't answer. Try again in a moment.")
        return str(e)
    log(f"ai -> {reply} ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    notch.done(reply)
    return reply


def handle_dictation(text, window):
    from settings import nectar_setting
    mode = nectar_setting("nectar-voice-dictation", S["dictation_mode"])
    label = "Typed"
    if mode == "polished" and not llm.online():
        mode, label = "raw", "Typed · offline, raw text"
    if mode == "raw":
        out = text.strip()  # exactly what Whisper heard
    else:
        out = textops.clean(textops.apply_aliases(text))
        if mode == "polished" and out:
            notch.thinking(out, "dictate")
            out = textops.polish(out, window.get("exe", ""))
    log(f"dictation mode {mode}")
    if not out:
        notch.clear()
        return ""
    if window.get("hwnd") and inject.foreground() != window["hwnd"]:
        inject.focus(window["hwnd"])
        time.sleep(0.08)
    # Add a space when continuing after earlier text on the same line.
    inject.paste_text(out + " ")
    notch.state("result", "dictate", out, label=label, hold_ms=2500)
    return out
