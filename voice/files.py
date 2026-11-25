"""Asking about a file: the one you dropped on the notch, copied (Ctrl+C), or selected in File Explorer.

Groq's free tier allows ~8k tokens per minute per model, and one request can't exceed that. So:
small files go in whole; long files are summarized in chunks spread over the models in parallel;
questions only send the passages that match.
"""
import concurrent.futures as cf
import json
import os
import re
import time

import inject
import llm
from common import S, log

LAST_DROP = os.path.join(os.environ.get("APPDATA", ""), "com.kua8.nectar", "last_drop.json")
CHUNK_CHARS = 16000          # ~4k tokens: leaves room for the instructions and the answer
MAX_CHUNKS = 6               # ~25 pages of dense text; longer files get a partial summary
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".tsx", ".html", ".css", ".java", ".c", ".cpp",
            ".h", ".rs", ".go", ".sql", ".xml", ".yml", ".yaml", ".ini", ".log", ".tex", ".rtf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


# ---- which file? --------------------------------------------------------------------------
def _dropped(max_age_s=1800):
    try:
        with open(LAST_DROP, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - d.get("time", 0) <= max_age_s:
            return [p for p in d.get("paths", []) if os.path.isfile(p)]
    except (OSError, ValueError):
        pass
    return []


def _clipboard_files():
    import win32clipboard
    import win32con
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                return [p for p in win32clipboard.GetClipboardData(win32con.CF_HDROP) if os.path.isfile(p)]
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    return []


def _explorer_selection(hwnd):
    """Files selected in the File Explorer window in front (or on the desktop)."""
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        for w in win32com.client.Dispatch("Shell.Application").Windows():
            try:
                if int(w.HWND) == int(hwnd):
                    return [it.Path for it in w.Document.SelectedItems() if os.path.isfile(it.Path)]
            except Exception:
                continue
    except Exception as e:
        log(f"explorer selection failed: {e}")
    return []


def find_files(hwnd=None):
    """Most relevant file(s) and where they came from."""
    if hwnd:
        sel = _explorer_selection(hwnd)
        if sel:
            return sel, "selected in File Explorer"
    dropped = _dropped()
    if dropped:
        return dropped, "dropped on the notch"
    clip = _clipboard_files()
    if clip:
        return clip, "copied"
    dropped = _dropped(max_age_s=7 * 24 * 3600)
    if dropped:
        return dropped, "dropped on the notch earlier"
    return [], ""


# ---- reading ------------------------------------------------------------------------------
def read_text(path):
    """(text, note). Images return ('', 'image'); unreadable types return ('', reason)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            import pymupdf
            doc = pymupdf.open(path)
            text = "\n".join(f"[page {i + 1}]\n{p.get_text()}" for i, p in enumerate(doc))
            if len(text.strip()) < 50 * max(1, len(doc)):
                return "", "scanned"
            return text, f"{len(doc)} pages"
        if ext == ".docx":
            import docx
            d = docx.Document(path)
            parts = [p.text for p in d.paragraphs]
            for t in d.tables:
                parts += [" | ".join(c.text for c in row.cells) for row in t.rows]
            return "\n".join(parts), "Word document"
        if ext == ".pptx":
            import pptx
            prs = pptx.Presentation(path)
            slides = []
            for i, s in enumerate(prs.slides, 1):
                bits = [sh.text_frame.text for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
                slides.append(f"[slide {i}] " + " / ".join(bits))
            return "\n".join(slides), f"{len(prs.slides)} slides"
        if ext in (".xlsx", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets[:5]:
                out.append(f"[sheet {ws.title}]")
                for row in ws.iter_rows(max_row=400, values_only=True):
                    if any(v is not None for v in row):
                        out.append(", ".join("" if v is None else str(v) for v in row))
            return "\n".join(out), "spreadsheet"
        if ext in IMAGE_EXT:
            return "", "image"
        if ext in TEXT_EXT or os.path.getsize(path) < 2_000_000:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read(), "text"
    except Exception as e:
        log(f"reading {path} failed: {e}")
        return "", f"couldn't read it ({e.__class__.__name__})"
    return "", "unsupported type"


def _image_question(path, question):
    from PIL import Image
    import screen
    img = Image.open(path)
    img.thumbnail((1600, 1600))
    return screen.ask_vision(question, img, system="You are looking at an image file the user shared. Answer their "
                                                    "request about it accurately and concisely.", max_tokens=700)


def _scanned_pdf(path, question, pages=3):
    """PDF without a text layer: look at the first pages as pictures."""
    import pymupdf
    from PIL import Image
    import io
    import screen
    doc = pymupdf.open(path)
    answers = []
    for i in range(min(pages, len(doc))):
        pix = doc[i].get_pixmap(dpi=110)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        answers.append(screen.ask_vision(f"(page {i + 1}) {question}", img, max_tokens=500))
    return "\n".join(answers)


# ---- answering ----------------------------------------------------------------------------
SYSTEM = ("You answer questions about a document the user shared. Use only the document. Be concise and concrete; "
          "for a summary give the key points. If the answer isn't in the text you were given, say so. Plain text "
          "only: no markdown symbols (no ** or #); for lists use short lines starting with '- '.")


def _chunks(text):
    return [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)]


def _relevant(text, question, budget=CHUNK_CHARS):
    """Passages that share the most words with the question, in document order."""
    import math
    words = {w for w in re.findall(r"[a-z]{3,}|\d+", question.lower())} - {
        "the", "what", "this", "that", "file", "document", "about", "does", "say", "happened", "tell", "from"}
    paras = []
    for p in re.split(r"\n", text):
        p = p.strip()
        while len(p) > 1500:  # very long lines/paragraphs: cut into pieces so they can still be picked
            paras.append(p[:1500])
            p = p[1500:]
        if p:
            paras.append(p)
    low = [p.lower() for p in paras]
    # Rare words count more (a word in every paragraph, like "section", says little; "6" says a lot).
    idf = {w: math.log((len(paras) + 1) / (1 + sum(1 for p in low if re.search(rf"\b{re.escape(w)}\b", p))))
           for w in words}
    score = [sum(idf[w] for w in words if re.search(rf"\b{re.escape(w)}\b", p)) for p in low]
    # A heading that matches pulls in the lines under it.
    for i in range(len(paras) - 1, 0, -1):
        if score[i - 1] > 0 and len(paras[i - 1]) < 80:
            score[i] += score[i - 1] * 0.5
    scored = sorted(range(len(paras)), key=lambda i: -score[i])
    keep, size = set(), 0
    for i in scored:
        if size + len(paras[i]) > budget:
            continue
        keep.add(i)
        size += len(paras[i])
    return "\n".join(paras[i] for i in sorted(keep))


def _ask(model, content, max_tokens=700):
    return llm.text(SYSTEM, content, temperature=0.2, max_tokens=max_tokens,
                    models=[model] + [m for m in S["groq_models"] if m != model])


def answer(question, path):
    name = os.path.basename(path)
    text, note = read_text(path)
    if note == "image":
        return _image_question(path, question)
    if note == "scanned":
        return _scanned_pdf(path, question)
    if not text.strip():
        return f"I couldn't read {name}: {note}."
    summary = re.search(r"\b(summar|overview|tl;?dr|key points|gist|what is this|what's this)", question, re.I)
    if len(text) <= CHUNK_CHARS:
        return _ask(S["groq_models"][0], f"Document: {name} ({note})\n\n{text}\n\nRequest: {question}")
    if not summary:
        return _ask(S["groq_models"][0], f"Document: {name} ({note}); these are the most relevant parts:\n\n"
                                         f"{_relevant(text, question)}\n\nRequest: {question}")
    # Long file, summary: one chunk per model at a time, in parallel, then combine.
    chunks = _chunks(text)
    partial = len(chunks) > MAX_CHUNKS
    chunks = chunks[:MAX_CHUNKS]
    models = S["groq_models"]
    notes = [None] * len(chunks)
    with cf.ThreadPoolExecutor(max_workers=len(models)) as ex:
        futs = {ex.submit(_ask, models[i % len(models)],
                          f"Part {i + 1} of {name}:\n\n{c}\n\nList the key points of this part in a few bullets.",
                          400): i for i, c in enumerate(chunks)}
        for f in cf.as_completed(futs):
            try:
                notes[futs[f]] = f.result()
            except Exception as e:
                notes[futs[f]] = f"(part {futs[f] + 1} couldn't be read: {e})"
    combined = "\n\n".join(f"Part {i + 1}:\n{n}" for i, n in enumerate(notes))
    out = _ask(models[0], f"Notes on each part of {name} ({note}):\n\n{combined}\n\nRequest: {question}", 700)
    return out + ("\n\n(Covered the first ~25 pages only.)" if partial else "")
