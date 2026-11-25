"""Answering a waiting notch question by voice: "yes", "send it", "no", "cancel", "the second one"...

Only short, clear answers count (the whole sentence must be one of these), so ordinary commands like
"stop the music" never get mistaken for "no". Works for Gizmo's own questions and for Claude Code's
permission prompts and multiple-choice questions: Nectar keeps them all and has a /answer route.
"""
import re

from rapidfuzz import fuzz

# An answer is a short sentence made ONLY of these words, and it must contain at least one "core" word.
# So "no, cancel that" and "yeah go ahead" count, but "yes open spotify" or "stop the music" never do.
FILLER = {"it", "that", "this", "please", "thanks", "thank", "you", "do", "to", "for", "go", "ahead", "me", "i",
          "would", "like", "sure", "right", "now"}
YES_CORE = {"yes", "yeah", "yep", "yup", "ok", "okay", "alright", "allow", "confirm", "proceed", "approve",
            "approved", "send", "correct", "sounds", "good", "haan", "ha", "kar", "de", "theek", "hai"}
NO_CORE = {"no", "nope", "nah", "cancel", "deny", "abort", "reject", "dont", "not", "never", "mind", "nevermind",
           "nahi", "mat", "karo", "karna", "stop", "allow"}
NO_ONLY = {"no", "nope", "nah", "cancel", "deny", "abort", "reject", "dont", "not", "never", "mind", "nevermind",
           "nahi", "mat", "stop"}  # one of these anywhere = a refusal ("allow" alone is not)
YES_PHRASES = {"do it", "please do", "go for it", "go ahead", "sure", "right", "yes"}
ORDINALS = {"first": 0, "1st": 0, "one": 0, "1": 0, "second": 1, "2nd": 1, "two": 1, "2": 1, "third": 2, "3rd": 2,
            "three": 2, "3": 2, "fourth": 3, "4th": 3, "four": 3, "4": 3}


def _norm(text):
    t = re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("’", "'"))
    t = t.replace("'", "")
    t = re.sub(r"\b(gizmo|um|uh|hey)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def classify(text, asks):
    """What does this utterance answer? Returns (allow: bool, choices: list[int], ask_id) or None.

    `asks` is Nectar's list of waiting questions (oldest first); the newest one gets the answer."""
    if not asks:
        return None
    ask = asks[-1]
    t = _norm(text)
    if not t or len(t.split()) > 6:
        return None
    questions = ask.get("questions") or []
    words = t.split()
    refusal = bool(words) and all(w in NO_CORE | FILLER | YES_CORE for w in words) and any(w in NO_ONLY for w in words)
    if refusal:
        return (False, [], ask["id"])
    if questions:
        # A multiple-choice question: "yes" means nothing, but "the second one" or the option's words do.
        if len(questions) != 1:
            return None  # several questions at once: answer by clicking
        options = questions[0].get("options") or []
        m = re.fullmatch(r"(?:option |number |choice |the )?(\w+)(?: one| option| choice)?", t)
        if m and m.group(1) in ORDINALS and ORDINALS[m.group(1)] < len(options):
            return (True, [ORDINALS[m.group(1)]], ask["id"])
        best = max(range(len(options)), key=lambda i: fuzz.ratio(t, _norm(options[i])), default=None)
        if best is not None and fuzz.ratio(t, _norm(options[best])) >= 80:
            return (True, [best], ask["id"])
        if best is not None and len(t) >= 3 and fuzz.partial_ratio(t, _norm(options[best])) >= 92 and \
                sum(fuzz.partial_ratio(t, _norm(o)) >= 92 for o in options) == 1:
            return (True, [best], ask["id"])
        return None
    if t in YES_PHRASES or (all(w in YES_CORE | FILLER for w in words) and any(w in YES_CORE for w in words)):
        return (True, [], ask["id"])
    return None
