"""The screen: what's on it (Groq vision), and acting on it (Windows UI Automation).

UI Automation is the accessibility layer screen readers use: it lists the buttons, links,
boxes and fields of the window in front by name, and can press them without moving the mouse.
"""
import base64
import io
import os
import re
import threading
import time

import win32api
import win32con
from PIL import ImageGrab
from rapidfuzz import fuzz

import llm
from common import S, log

# Never pressed without a yes in the notch.
DANGER = re.compile(r"\b(buy|pay|purchase|place (your )?order|checkout|check out|confirm (payment|order)|"
                    r"send|delete|remove account|transfer|subscribe|donate|submit payment|book now)\b", re.I)
CLICKABLE = {"ButtonControl", "HyperlinkControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl",
             "ListItemControl", "MenuItemControl", "TabItemControl", "TreeItemControl", "SplitButtonControl",
             "EditControl", "ImageControl", "TextControl", "DocumentControl"}


# ---- capture & vision ---------------------------------------------------------------------
def monitor_rect(hwnd=None):
    try:
        mon = win32api.MonitorFromWindow(hwnd or 0, win32con.MONITOR_DEFAULTTOPRIMARY)
        return win32api.GetMonitorInfo(mon)["Monitor"]
    except Exception:
        return None


def capture(hwnd=None, max_dim=1600):
    rect = monitor_rect(hwnd)
    img = ImageGrab.grab(bbox=rect, all_screens=True) if rect else ImageGrab.grab()
    full = img.copy()
    img.thumbnail((max_dim, max_dim))
    return img, full


def data_url(img, quality=82):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ask_vision(question, img, system=None, max_tokens=500):
    system = system or ("You look at a screenshot of the user's Windows screen and answer their question about it "
                        "briefly (one to three sentences; it shows in a small notch). Be specific and only say what "
                        "you can actually see.")
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": question},
                                         {"type": "image_url", "image_url": {"url": data_url(img)}}]}]
    msg = llm.chat(msgs, temperature=0.1, max_tokens=max_tokens, models=[S["vision_model"]])
    return (msg.get("content") or "").strip()


# ---- UI Automation ------------------------------------------------------------------------
def _uia():
    import uiautomation as auto
    return auto


_woken = set()


def wake_chromium(hwnd):
    """Chromium/Electron apps (Claude, Discord, VS Code, Spotify, WhatsApp...) only build their full
    accessibility tree once a screen reader asks for it. Asking each content window for its
    accessible object (what screen readers do) switches it on; the next look sees every control."""
    import win32gui
    import ctypes
    from ctypes import wintypes
    if hwnd in _woken:
        return False
    found = []

    def cb(child, _):
        if win32gui.GetClassName(child) == "Chrome_RenderWidgetHostHWND":
            found.append(child)
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        return False
    if not found:
        return False
    oleacc = ctypes.windll.oleacc
    iid = (ctypes.c_byte * 16)(*bytes.fromhex("e0 da 1f 61 79 a6 cf 11 88 a5 00 aa 00 bb 0e 85".replace(" ", "")))
    for child in found:
        ptr = ctypes.c_void_p()
        oleacc.AccessibleObjectFromWindow(wintypes.HWND(child), wintypes.DWORD(0xFFFFFFFC), ctypes.byref(iid),
                                          ctypes.byref(ptr))  # OBJID_CLIENT, IID_IAccessible
    _woken.add(hwnd)
    return True


def elements(hwnd, limit=400, budget_s=2.5):
    """Named, visible controls in the window: [{'name','type','rect','ctrl'}] in reading order."""
    auto = _uia()
    if wake_chromium(hwnd):
        time.sleep(0.6)  # give it a moment to build the tree
    root = auto.ControlFromHandle(hwnd)
    if not root:
        return []
    out, t0 = [], time.time()
    for ctrl, depth in auto.WalkControl(root, includeTop=False, maxDepth=40):
        if time.time() - t0 > budget_s or len(out) >= limit:
            break
        try:
            if ctrl.ControlTypeName not in CLICKABLE or ctrl.IsOffscreen:
                continue
            name = (ctrl.Name or "").strip()
            if ctrl.ControlTypeName == "EditControl" and not name:
                name = (ctrl.GetPropertyValue(30159) or "").strip()  # placeholder / help text
            if not name or len(name) > 300:
                continue
            r = ctrl.BoundingRectangle
            if r.width() < 3 or r.height() < 3:
                continue
            out.append({"name": name, "type": ctrl.ControlTypeName.replace("Control", ""), "rect": r, "ctrl": ctrl})
        except Exception:
            continue
    return out


def _kind_ok(el, kind):
    if not kind:
        return el["type"] not in ("Text", "Document", "Image") or len(el["name"]) < 60
    return {"field": el["type"] in ("Edit", "ComboBox", "Document"),
            "checkbox": el["type"] in ("CheckBox", "RadioButton", "ListItem"),
            "link": el["type"] in ("Hyperlink", "ListItem", "TreeItem"),
            "tab": el["type"] in ("TabItem", "ListItem")}.get(kind, True)


def find(target, els, kind=None, goal=""):
    """Best control for a spoken description. Fuzzy name match first; the AI picks when it's unclear."""
    cands = [e for e in els if _kind_ok(e, kind)]
    if not cands:
        return None
    t = target.lower().strip()
    scored = sorted(((max(fuzz.ratio(t, e["name"].lower()), fuzz.partial_ratio(t, e["name"].lower()) - 5), i)
                     for i, e in enumerate(cands)), reverse=True)
    best, second = scored[0], (scored[1] if len(scored) > 1 else (0, -1))
    if best[0] >= 90 and best[0] - second[0] >= 6:
        return cands[best[1]]
    # Let the model choose from the top matches (it understands "the first video", "sign in" vs "Log in").
    top = [cands[i] for _, i in scored[:60]]
    listing = "\n".join(f"{n}. [{e['type']}] {e['name'][:120]}" for n, e in enumerate(top))
    try:
        reply = llm.text("Pick the on-screen control the user means. Reply with only its number, or -1 if none fits.",
                         f"User wants: {goal or target}\nControls (in screen order):\n{listing}",
                         temperature=0, max_tokens=8)
        n = int(re.search(r"-?\d+", reply).group())
        return top[n] if 0 <= n < len(top) else None
    except Exception as e:
        log(f"element pick failed: {e}")
        return cands[best[1]] if best[0] >= 75 else None


def press(el, show=True):
    """Use the control's own action (no mouse) when it has one; otherwise click its middle.
    The AI cursor glides there first so you can see what's being clicked."""
    c = el["ctrl"]
    r = el["rect"]
    if show:
        import cursor
        cursor.glide_to((r.left + r.right) // 2, (r.top + r.bottom) // 2)
        cursor.click()
    for getter, action in (("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"),
                           ("GetSelectionItemPattern", "Select"), ("GetExpandCollapsePattern", "Expand")):
        try:
            pat = getattr(c, getter)()
            if pat:
                getattr(pat, action)()
                return
        except Exception:
            continue
    r = el["rect"]
    _uia().Click((r.left + r.right) // 2, (r.top + r.bottom) // 2, waitTime=0.05)


def with_uia(fn, *a, **kw):
    """UI Automation needs COM set up on the calling thread."""
    auto = _uia()
    result = {}

    def run():
        with auto.UIAutomationInitializerInThread():
            try:
                result["v"] = fn(*a, **kw)
            except Exception as e:
                result["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(30)
    if "e" in result:
        raise result["e"]
    return result.get("v")


def focused_box():
    """What has keyboard focus: {'editable': bool, 'selected': str, 'name': str}. editable=None if unknown."""
    def run():
        auto = _uia()
        c = auto.GetFocusedControl()
        if not c:
            return {"editable": None, "selected": "", "name": ""}
        kind = c.ControlTypeName
        editable = kind in ("EditControl", "DocumentControl", "ComboBoxControl")
        if not editable:
            try:  # rich editors (Gmail body, Notion, Word) often expose a writable Value or Text pattern
                vp = c.GetValuePattern()
                editable = bool(vp) and not vp.IsReadOnly
            except Exception:
                pass
        selected = ""
        try:
            tp = c.GetTextPattern()
            if tp:
                sel = tp.GetSelection()
                selected = "".join(r.GetText(2000) for r in sel) if sel else ""
        except Exception:
            pass
        return {"editable": editable, "selected": selected, "name": (c.Name or kind)[:40]}
    try:
        return with_uia(run) or {"editable": None, "selected": "", "name": ""}
    except Exception:
        return {"editable": None, "selected": "", "name": ""}


def browser_url(hwnd):
    """The address bar text of a Chromium browser window (Brave/Chrome/Edge), or ''."""
    def run():
        auto = _uia()
        root = auto.ControlFromHandle(hwnd)
        bar = root.EditControl(searchDepth=12, RegexName=r"(?i)address|search bar|url")
        if bar.Exists(0.5, 0.1):
            try:
                return bar.GetValuePattern().Value
            except Exception:
                return ""
        return ""
    try:
        return with_uia(run) or ""
    except Exception:
        return ""


def pictures_dir(*parts):
    base = os.path.join(os.environ.get("USERPROFILE", ""), "Pictures", *parts)
    os.makedirs(base, exist_ok=True)
    return base
