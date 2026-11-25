"""A visible "AI cursor": glides to what voice is about to click, shows a click ripple, then fades.

It's a tiny always-on-top window that mouse clicks pass straight through, so it never gets in the
way and your own mouse stays where it is. Runs its own Tk thread; call glide_to() / click() from anywhere.
"""
import ctypes
import queue
import threading
import time

LABEL = "Gizmo"
ACCENT = "#5ac8fa"
KEY = "#010203"       # transparent colour
W, H = 190, 70

_q = queue.Queue()
_started = threading.Event()
_ready = threading.Event()


def _style_click_through(hwnd):
    GWL_EXSTYLE = -20
    WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = \
        0x80000, 0x20, 0x80, 0x8000000, 0x8
    u = ctypes.windll.user32
    style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    u.SetWindowLongW(hwnd, GWL_EXSTYLE,
                     style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)


def _run():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-transparentcolor", KEY)
    win.configure(bg=KEY)
    c = tk.Canvas(win, width=W, height=H, bg=KEY, highlightthickness=0)
    c.pack()
    # Arrow (tip at 6,6), white with a dark outline, plus a name pill.
    arrow = [6, 6, 6, 30, 12, 24, 17, 35, 21, 33, 16, 22, 25, 22]
    c.create_polygon(arrow, fill="white", outline="#111", width=1.5)
    c.create_rectangle(26, 30, 26 + 9 * len(LABEL) + 14, 52, fill=ACCENT, outline=ACCENT)
    c.create_text(33, 41, text=LABEL, anchor="w", fill="#04121a", font=("Segoe UI Semibold", 10))
    ring = c.create_oval(6, 6, 6, 6, outline=ACCENT, width=2)
    win.withdraw()
    win.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
    _style_click_through(hwnd)
    state = {"x": None, "y": None, "hide_at": 0}

    def place(x, y):
        win.geometry(f"{W}x{H}+{int(x) - 6}+{int(y) - 6}")

    def tick():
        try:
            while True:
                cmd, a = _q.get_nowait()
                if cmd == "glide":
                    x1, y1, dur, done = a
                    if state["x"] is None:
                        pt = ctypes.wintypes.POINT()
                        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                        state["x"], state["y"] = pt.x, pt.y
                    x0, y0, t0 = state["x"], state["y"], time.time()
                    win.deiconify()
                    win.attributes("-topmost", True)

                    def step():
                        k = min(1.0, (time.time() - t0) / dur)
                        e = 1 - (1 - k) ** 3  # ease out
                        state["x"], state["y"] = x0 + (x1 - x0) * e, y0 + (y1 - y0) * e
                        place(state["x"], state["y"])
                        if k < 1:
                            root.after(12, step)
                        else:
                            done.set()
                    step()
                elif cmd == "click":
                    t0 = time.time()

                    def ripple():
                        k = min(1.0, (time.time() - t0) / 0.45)
                        r = 4 + 16 * k
                        c.coords(ring, 6 - r, 6 - r, 6 + r, 6 + r)
                        c.itemconfigure(ring, state="normal" if k < 1 else "hidden")
                        if k < 1:
                            root.after(15, ripple)
                    ripple()
                    state["hide_at"] = time.time() + a
                elif cmd == "hide":
                    state["hide_at"] = time.time()
        except queue.Empty:
            pass
        if state["hide_at"] and time.time() >= state["hide_at"]:
            win.withdraw()
            state.update(hide_at=0, x=None, y=None)
        root.after(15, tick)

    root.after(15, tick)
    _ready.set()
    root.mainloop()


def _ensure():
    if not _started.is_set():
        _started.set()
        threading.Thread(target=_run, daemon=True).start()
    _ready.wait(3)


def glide_to(x, y, duration=0.45):
    """Move the AI cursor to screen pixel (x, y); returns when it arrives."""
    try:
        import ctypes.wintypes  # noqa: F401
        _ensure()
        done = threading.Event()
        _q.put(("glide", (x, y, duration, done)))
        done.wait(duration + 1)
    except Exception:
        pass


def click(linger=1.2):
    """Show the click ripple; the cursor fades `linger` seconds later."""
    try:
        _ensure()
        _q.put(("click", linger))
    except Exception:
        pass


def hide():
    _q.put(("hide", None))
