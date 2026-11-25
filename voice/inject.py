"""Putting text into other apps, reading the selection, pressing keys."""
import ctypes
import time

import keyboard
import win32clipboard
import win32con
import win32gui
import win32process

user32 = ctypes.windll.user32


def foreground():
    return win32gui.GetForegroundWindow()


def window_info(hwnd=None):
    hwnd = hwnd or foreground()
    try:
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        import psutil
        exe = psutil.Process(pid).name()
    except Exception:
        title, exe = "", ""
    return {"hwnd": hwnd, "title": title, "exe": exe}


def get_clipboard():
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                return None
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.03)
    return None


def set_clipboard(text):
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                if text is not None:
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.03)
    return False


def _release_modifiers():
    for k in ("right ctrl", "left ctrl", "right alt", "left alt", "shift", "left windows"):
        if keyboard.is_pressed(k):
            keyboard.release(k)


def paste_text(text, restore=True):
    """Paste through the clipboard (fast, works in almost every app), then put the old clipboard back."""
    old = get_clipboard() if restore else None
    set_clipboard(text)
    _release_modifiers()
    time.sleep(0.03)
    keyboard.send("ctrl+v")
    if restore:
        time.sleep(0.35)  # let the target app read it first
        if old is not None:
            set_clipboard(old)


def get_selection(hwnd=None):
    """Copy the current selection without losing the user's clipboard. '' when nothing is selected."""
    if hwnd and foreground() != hwnd:
        focus(hwnd)
        time.sleep(0.12)
    old = get_clipboard()
    marker = "⁣nectar-voice⁣"
    set_clipboard(marker)
    _release_modifiers()
    time.sleep(0.03)
    keyboard.send("ctrl+c")
    text = ""
    for _ in range(20):
        time.sleep(0.03)
        cur = get_clipboard()
        if cur is not None and cur != marker:
            text = cur
            break
    set_clipboard(old) if old is not None else set_clipboard("")
    return text


def press(combo):
    _release_modifiers()
    keyboard.send(combo)


def focus(hwnd):
    """Bring a window to the front. Never taps Alt: that opens menu bars (Notepad, Electron apps)
    and the next Ctrl+C / Ctrl+V would go to the menu instead of the text."""
    if not hwnd:
        return False
    try:
        if foreground() == hwnd:
            return True
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        fg = foreground()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(me, fg_thread, False)
        return foreground() == hwnd
    except Exception:
        return False
