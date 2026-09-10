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
