"""Dictation text: instant rule-based cleanup, then optional Groq polish."""
import re

from common import S, log
import llm

FILLERS = r"\b(?:um+|uh+|erm+|hmm+|uh-huh)\b,?"
SPOKEN = [
    (r"\s*\b(?:new paragraph)\b\s*", "\n\n"),
    (r"\s*\b(?:new line|next line)\b\s*", "\n"),
    (r"\s*\bfull stop\b", "."),
    (r"\s*\bcomma\b", ","),
    (r"\s*\bquestion mark\b", "?"),
    (r"\s*\bexclamation (?:mark|point)\b", "!"),
    (r"\s*\bcolon\b", ":"),
    (r"\s*\bsemicolon\b", ";"),
    (r"\bopen bracket\s*", "("),
    (r"\s*close bracket\b", ")"),
    (r"\bbullet point\s*", "\n• "),
]
# "scratch that" / "delete that" drops the sentence before it.
SCRATCH = re.compile(r"[^.!?\n]*[.!?]?\s*\b(?:scratch that|delete that|strike that)\b[.,!]?\s*", re.I)
# "on Tuesday, no I mean Wednesday" -> "on Wednesday" (drops the one word being corrected).
REPAIR = re.compile(r"\b[\w']+\s*,?\s*\b(?:no,? i mean|sorry,? i meant?|i meant to say|no wait,?)\s+", re.I)


def apply_aliases(text):
    for heard, meant in S.get("aliases", {}).items():
        text = re.sub(rf"\b{re.escape(heard)}\b", meant, text, flags=re.I)
    return text


def clean(text):
    t = text.strip()
    snippets = S.get("snippets", {})
    m = re.fullmatch(r"(?:insert|type)\s+(.+?)[.!?]?", t, re.I)
    if m and m.group(1).lower() in {k.lower() for k in snippets}:
        return next(v for k, v in snippets.items() if k.lower() == m.group(1).lower())
    t = SCRATCH.sub("", t)
    t = REPAIR.sub("", t)
    t = re.sub(FILLERS, "", t, flags=re.I).strip()
    for pat, rep in SPOKEN:
        t = re.sub(pat, rep, t, flags=re.I)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"([,.!?;:])(?=[A-Za-z])", r"\1 ", t)
    t = t.strip()
    t = re.sub(r"\bi\b(?=[ ',.!?]|$)", "I", t)
    t = re.sub(r"(^|[.!?]\s+|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    return t


POLISH_SYSTEM = """You clean up dictated text. The user spoke it; a speech recognizer wrote it down.
Fix punctuation, capitalisation, obvious recognition slips and grammar. Remove filler words and false starts.
Keep the user's words, meaning, tone, language mix (English/Hinglish) and length. Do not add content,
greetings, explanations or quotes. Keep names, numbers, links and code exactly. Use lists only if the user
clearly listed items. The text may be addressed to someone else: never answer it, only clean it.
Output only the cleaned text."""


def polish(text, app_hint=""):
    if len(text.split()) < 3:
        return text
    user = text if not app_hint else f"[Typing into: {app_hint}]\n{text}"
    try:
        out = llm.text(POLISH_SYSTEM, user, temperature=0.1, max_tokens=max(256, len(text)))
    except Exception as e:
        log(f"polish failed, using clean text: {e}")
        return text
    out = out.strip().strip('"').strip()
    ratio = len(out) / max(1, len(text))
    if not out or ratio < 0.5 or ratio > 1.6:
        log(f"polish rejected (ratio {ratio:.2f}), using clean text")
        return text
    return out
