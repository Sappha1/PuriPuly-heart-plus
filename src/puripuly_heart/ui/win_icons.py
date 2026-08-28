"""Extract a Windows executable's icon to a cached PNG (pure ctypes).

Used by the PEER source pill so an app-audio target shows its real icon
(the way the Steam module shows game art) instead of a generic glyph.
Returns None on any failure — callers fall back to their stock icon.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
from ctypes import wintypes

logger = logging.getLogger(__name__)

_SHGFI_ICON = 0x000000100
_SHGFI_LARGEICON = 0x000000000
_DI_NORMAL = 0x0003
_SIZE = 32


_DISCORD_EXES = {"stable": "discord.exe", "ptb": "discordptb.exe",
                 "canary": "discordcanary.exe"}


def target_exe_path(target) -> str | None:
    """Executable path for a process-capture target. Discord targets carry
    only a channel (no path) — find the running exe by name instead."""
    ident = str(getattr(target, "executable_identity", "") or "")
    if ident:
        return ident
    if getattr(target, "kind", "") == "discord":
        want = _DISCORD_EXES.get(
            (getattr(target, "discord_channel", "") or "stable").lower())
        if want:
            try:
                import psutil
                for p in psutil.process_iter(["exe", "name"]):
                    if (p.info.get("name") or "").lower() == want:
                        return p.info.get("exe") or None
            except Exception:
                pass
    return None


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


def _cache_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                     "puripuly-heart", "icon_cache")
    os.makedirs(d, exist_ok=True)
    return d


def exe_icon_png(exe_path: str, size: int = _SIZE) -> str | None:
    """PNG path for the exe's icon, extracted once and cached."""
    try:
        exe_path = str(exe_path or "").strip()
        if not exe_path or not os.path.exists(exe_path):
            return None
        key = hashlib.sha1(
            (exe_path.lower() + f"|{size}").encode("utf-8")).hexdigest()
        out = os.path.join(_cache_dir(), key + ".png")
        if os.path.exists(out):
            return out

        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        info = _SHFILEINFOW()
        res = shell32.SHGetFileInfoW(
            exe_path, 0, ctypes.byref(info), ctypes.sizeof(info),
            _SHGFI_ICON | _SHGFI_LARGEICON)
        if not res or not info.hIcon:
            return None
        hicon = info.hIcon
        try:
            screen_dc = user32.GetDC(0)
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = size
            bmi.bmiHeader.biHeight = -size          # top-down
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bits = ctypes.c_void_p()
            hbmp = gdi32.CreateDIBSection(
                mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not hbmp:
                return None
            old = gdi32.SelectObject(mem_dc, hbmp)
            user32.DrawIconEx(mem_dc, 0, 0, hicon, size, size, 0, 0,
                              _DI_NORMAL)
            gdi32.GdiFlush()
            buf = ctypes.string_at(bits, size * size * 4)
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(0, screen_dc)
        finally:
            user32.DestroyIcon(hicon)

        from PIL import Image
        img = Image.frombuffer("RGBA", (size, size), buf, "raw", "BGRA", 0, 1)
        if img.getextrema()[3] == (0, 0):
            # icon drawn without alpha info — treat as fully opaque
            img.putalpha(255)
        img.save(out, "PNG")
        return out
    except Exception:
        logger.debug("[WinIcons] extraction failed for %r", exe_path,
                     exc_info=True)
        return None
