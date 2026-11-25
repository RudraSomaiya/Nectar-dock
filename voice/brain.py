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
}

ROUTER_SYSTEM = """You are Nectar Voice, a voice assistant on the user's Windows laptop. The user spoke a command;
speech recognition may have small mistakes (e.g. "brief" = Brave, "cloud" = Claude), so read for intent.
Use the tools to act. For several steps, call the tools in order (you may call more than one, or call again
after seeing results). If the user only asks a question, answer it directly in one or two short sentences
(it shows in a small notch). Never claim you did something a tool didn't do. If a request is unclear or
needs something no tool can do, say so briefly. Reply in the language the user used; for Hindi or
Hinglish, reply in romanized Hinglish (Latin letters), never Devanagari.
Prefer one direct tool over many small ones (e.g. youtube_play instead of open browser + search + click).
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
        log(f"tool {name} {json.dumps(args)}")
        return str(t["fn"](**args))
    except TypeError as e:
        return f"(bad arguments for {name}: {e})"
    except Exception as e:
        log(f"tool {name} failed: {e}")
        return f"(failed: {e})"


def route_with_ai(text, window):
    cats = _categories(text)
    schemas = tools.schemas(cats)
    ctx = f"Now: {_now()}. Front window: {window.get('title') or '?'} ({window.get('exe') or '?'})."
    messages = [{"role": "system", "content": ROUTER_SYSTEM + "\n" + ctx}, {"role": "user", "content": text}]
    results = []
    for _ in range(6):
        msg = llm.chat(messages, tools=schemas, temperature=0.2, max_tokens=700)
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

    # 1. "make this longer" with text selected: rewrite the selection, any wording.
    norm = rules.normalize(text)
    if EDIT_RE.search(norm):
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

    # 2. Instant rules.
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
    text = textops.apply_aliases(text)
    out = textops.clean(text)
    if S["dictation_mode"] == "polished" and out:
        notch.thinking(out, "dictate")
        out = textops.polish(out, window.get("exe", ""))
    if not out:
        notch.clear()
        return ""
    if window.get("hwnd") and inject.foreground() != window["hwnd"]:
        inject.focus(window["hwnd"])
        time.sleep(0.08)
    # Add a space when continuing after earlier text on the same line.
    inject.paste_text(out + " ")
    notch.state("result", "dictate", out, label="Typed", hold_ms=2500)
    return out
