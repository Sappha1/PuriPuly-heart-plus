"""Boot-splash progress + teardown (r643).

PyInstaller's Splash target injects a ``pyi_splash`` module whose only live
control is ``update_text()``. The user asked for at least a moving percentage,
so a short percentage string is animated from a background thread and drawn by
pyi_splash. It is deliberately JUST the number (no bar): the r641 block-glyph
bar looked poor and a wide string is hard to keep centred; a short percentage
stays near-centre. Everything is a no-op in a source run (no ``pyi_splash``).

Own module (like ``boot_stealth``) so the frozen ``__main__`` entry and
``import puripuly_heart.main`` share one instance.
"""
from __future__ import annotations

import contextlib
import threading

_stop = threading.Event()
_thread: threading.Thread | None = None
_closed = False
_last_alpha_frac = 0.85  # r653: remembered so reveal() can re-assert it via Win32


def _pyi():
    try:
        import pyi_splash  # type: ignore
        return pyi_splash
    except Exception:
        return None


def render_pct(pct: int) -> str:
    """The status string: just the percentage, e.g. '45%'."""
    return f"{max(0, min(100, int(pct)))}%"


def start_progress(half_life_s: float = 1.6) -> None:
    """Animate the percentage on a DECELERATING curve toward ~99 %, so it climbs
    fast at first then slows and keeps inching up - never a hard cap the user
    sees it stick at (r643 report: the old fixed ramp froze at 92 %). close()
    finishes it at 100 %. No-op in source runs or if already started."""
    ps = _pyi()
    if ps is None:
        return
    global _thread
    if _thread is not None:
        return

    def _run() -> None:
        import time
        t0 = time.monotonic()
        last = -1
        while not _stop.is_set():
            elapsed = time.monotonic() - t0
            # 0 -> ~50% at one half-life, ~75% at two, approaching 99 slowly
            pct = min(99, int(99 * (1.0 - 0.5 ** (elapsed / half_life_s))))
            if pct != last:
                last = pct
                with contextlib.suppress(Exception):
                    ps.update_text(render_pct(pct))
            if _stop.wait(0.08):
                return

    _thread = threading.Thread(target=_run, name="boot-splash", daemon=True)
    _thread.start()


def reveal() -> None:
    """Show the splash (r652). The build bakes it WITHDRAWN so a disabled splash
    never flashes (open-then-close); the app deiconifies it here only when the
    setting is on. Frozen-only, best-effort - drives a custom ``reveal``
    procedure injected into the splash tcl by build.spec. No-op in a source run
    or against an older bundle (which was not baked withdrawn, so it was already
    visible)."""
    ps = _pyi()
    if ps is None:
        return
    with contextlib.suppress(Exception):
        ps._send_command("reveal", [])
    # Re-assert the layered alpha once the window maps. The tcl deiconify is
    # processed asynchronously, so apply now AND once shortly after to win over
    # any alpha reset during the map (Win32, so it does not depend on Tk).
    _apply_alpha_win32(_last_alpha_frac)

    def _reassert() -> None:
        import time
        time.sleep(0.30)
        _apply_alpha_win32(_last_alpha_frac)

    with contextlib.suppress(Exception):
        threading.Thread(target=_reassert, name="boot-splash-alpha",
                         daemon=True).start()


def set_alpha(opacity_pct: float) -> None:
    """Set the splash window opacity (percent, 20..100) at launch (r652).

    Applied while the splash is still WITHDRAWN, just before reveal(), so the
    window appears at the user's Settings > Updates opacity (an 85 % default is
    also baked in). Like the show-boot-splash toggle, a changed value takes
    effect from the next launch. Frozen-only, best-effort - it drives a custom
    ``set_alpha`` procedure injected into the splash tcl by build.spec via the
    same IPC socket ``update_text`` uses. No-op in a source run or if the
    handler is absent (an older bundle) - the splash just stays at its default.
    """
    ps = _pyi()
    if ps is None:
        return
    try:
        frac = max(0.2, min(1.0, float(opacity_pct) / 100.0))
    except Exception:
        return
    global _last_alpha_frac
    _last_alpha_frac = frac
    # Ask the splash tcl to set -alpha (works where Tk renders layered alpha)...
    with contextlib.suppress(Exception):
        ps._send_command("set_alpha", [f"{frac:.3f}"])
    # ...and, on Windows, ALSO set the layered-window alpha directly on the
    # splash HWND. The bootloader's bundled Tk did not render `-alpha` on the
    # user's machine (the window stayed opaque at every value), but Windows'
    # own SetLayeredWindowAttributes(LWA_ALPHA) does composite it - so we drive
    # it ourselves, bypassing Tk. The splash shares this process, so we find it
    # by enumerating our own top-level windows. Best-effort, frozen-only.
    _apply_alpha_win32(frac)


def _apply_alpha_win32(frac: float) -> None:
    """Set the splash window's layered-alpha via Win32, independent of Tk.

    Finds this process's small always-on-top popup (the bootloader splash - the
    only such window at startup, before the main window exists) and applies
    SetLayeredWindowAttributes. Fully guarded; a no-op off Windows or on any
    failure."""
    import sys
    if not sys.platform.startswith("win") or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return
    try:
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        GWL_STYLE, GWL_EXSTYLE = -16, -20
        WS_POPUP = 0x80000000
        WS_EX_TOPMOST, WS_EX_LAYERED = 0x00000008, 0x00080000
        LWA_ALPHA = 0x2
        cur_pid = k32.GetCurrentProcessId()
        alpha_byte = max(0, min(255, int(round(frac * 255))))

        GetWindowLong = u32.GetWindowLongW
        GetWindowLong.restype = ctypes.c_long
        GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
        SetWindowLong = u32.SetWindowLongW
        SetWindowLong.restype = ctypes.c_long
        SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        SetLWA = u32.SetLayeredWindowAttributes
        SetLWA.restype = wintypes.BOOL
        SetLWA.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_byte, wintypes.DWORD]

        class RECT(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                        ("r", ctypes.c_long), ("b", ctypes.c_long)]

        def _to_long(v: int) -> int:
            v &= 0xFFFFFFFF
            return v - 0x100000000 if v >= 0x80000000 else v

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lp):
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != cur_pid:
                return True
            style = GetWindowLong(hwnd, GWL_STYLE) & 0xFFFFFFFF
            ex = GetWindowLong(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
            # the splash is an overrideredirect (WS_POPUP) always-on-top window
            if not (style & WS_POPUP) or not (ex & WS_EX_TOPMOST):
                return True
            rc = RECT()
            if u32.GetWindowRect(hwnd, ctypes.byref(rc)):
                w, h = rc.r - rc.l, rc.b - rc.t
                if w <= 0 or h <= 0 or w > 900 or h > 700:
                    return True  # not the small splash
            if not (ex & WS_EX_LAYERED):
                SetWindowLong(hwnd, GWL_EXSTYLE, _to_long(ex | WS_EX_LAYERED))
            SetLWA(hwnd, 0, ctypes.c_byte(alpha_byte), LWA_ALPHA)
            return True

        u32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass


def close() -> None:
    """Close the splash IMMEDIATELY - the app is ready, so no lingering progress
    (r645 held 100% for a moment; the user rightly did not want to see the
    splash after the window was already up). Frozen-only, idempotent - every
    reveal path and the duplicate-instance early exit call it. The percentage
    just climbs during load and vanishes with the splash the instant the window
    is ready."""
    global _closed
    if _closed:
        return
    _closed = True
    _stop.set()
    ps = _pyi()
    if ps is None:
        return
    with contextlib.suppress(Exception):
        ps.close()
