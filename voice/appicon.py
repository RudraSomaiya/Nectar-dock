"""An app's own icon as a small PNG data URL, from its app ID (the ID notifications carry).

Windows hands out the icon of anything in the Start menu's app list through the shell item
"shell:AppsFolder\\<app id>" (IShellItemImageFactory). Works for Store apps and desktop apps alike.
Icons are cached on disk.
"""
import base64
import ctypes
import hashlib
import io
import os
from ctypes import wintypes

import comtypes
from comtypes import GUID, COMMETHOD, HRESULT, IUnknown

from common import data_path, log

CACHE = data_path("icons")
os.makedirs(CACHE, exist_ok=True)
SIZE_PX = 40


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class IShellItemImageFactory(IUnknown):
    _iid_ = GUID("{bcc18b79-ba16-442f-80c4-8a59c30c463b}")
    _methods_ = [COMMETHOD([], HRESULT, "GetImage",
                           (["in"], SIZE, "size"), (["in"], ctypes.c_int, "flags"),
                           (["out"], ctypes.POINTER(wintypes.HBITMAP), "phbm"))]


_shell32 = ctypes.windll.shell32
_shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID),
                                                 ctypes.POINTER(ctypes.c_void_p)]


def _hbitmap_to_png(hbm):
    from PIL import Image
    gdi = ctypes.windll.gdi32

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                    ("bmWidthBytes", ctypes.c_long), ("bmPlanes", ctypes.c_ushort),
                    ("bmBitsPixel", ctypes.c_ushort), ("bmBits", ctypes.c_void_p)]

    bm = BITMAP()
    gdi.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm))
    w, h = bm.bmWidth, bm.bmHeight
    buf = ctypes.create_string_buffer(w * h * 4)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

    bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    hdc = ctypes.windll.user32.GetDC(0)
    gdi.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bih), 0)
    ctypes.windll.user32.ReleaseDC(0, hdc)
    gdi.DeleteObject(hbm)
    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def icon_data_url(app_id):
    """'data:image/png;base64,…' for the app, or None."""
    if not app_id:
        return None
    path = os.path.join(CACHE, hashlib.sha1(app_id.lower().encode()).hexdigest()[:16] + ".png")
    if not os.path.exists(path):
        try:
            comtypes.CoInitialize()
            ptr = ctypes.c_void_p()
            iid = IShellItemImageFactory._iid_
            hr = _shell32.SHCreateItemFromParsingName(f"shell:AppsFolder\\{app_id}", None, ctypes.byref(iid),
                                                      ctypes.byref(ptr))
            if hr != 0 or not ptr.value:
                return None
            factory = ctypes.cast(ptr, ctypes.POINTER(IShellItemImageFactory))
            hbm = factory.GetImage(SIZE(SIZE_PX, SIZE_PX), 0x1)  # SIIGBF_BIGGERSIZEOK
            with open(path, "wb") as f:
                f.write(_hbitmap_to_png(hbm))
        except Exception as e:
            log(f"icon for {app_id} failed: {e}")
            return None
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()
