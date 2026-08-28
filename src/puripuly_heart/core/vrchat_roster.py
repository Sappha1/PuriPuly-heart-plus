"""Live "who is in the VRChat room" roster, tailed from VRChat's own log.

VRChat writes "OnPlayerJoined <name>" / "OnPlayerLeft <name>" to
output_log_*.txt under LocalLow (the same source VRCX reads). The OCR
overlay tails it inside its own subprocess for nameplate filtering; this is
the MAIN-process equivalent — tiny and dependency-free — so features like
speaker-ID can prefer people who are actually in the room.

The roster is advisory: `active_names()` returns None whenever VRChat is
not writing its log, and consumers must then apply NO roster rules at all —
voices from Discord or any other app must never be penalized just because
VRChat is closed.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)

_LOG_DIR = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow",
                        "VRChat", "VRChat")
_JOIN_RE = re.compile(r"OnPlayerJoined\s+(.+?)(?:\s+\(usr_[^)]*\))?\s*$")
_LEFT_RE = re.compile(r"OnPlayerLeft\s+(.+?)(?:\s+\(usr_[^)]*\))?\s*$")
# a world change empties the room; everyone present re-logs a join
_WORLD_RE = re.compile(r"Joining\s+(?:wrld_|or\s+[Cc]reating\s+[Rr]oom)")
# the roster counts as live only while VRChat keeps writing the log
_STALE_SECONDS = 600.0


def norm_name(name: str) -> str:
    """Match the OCR roster's normalization (alnum-only, casefolded) so
    display-name decorations never break equality."""
    return "".join(ch for ch in (name or "") if ch.isalnum()).casefold()


class VRChatRoster:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._names: dict[str, str] = {}   # norm -> display
        self._log_mtime = 0.0
        self._last_logged_count = -1
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="vrchat-roster", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def active_names(self) -> frozenset[str] | None:
        """Normalized names currently in the room, or None when VRChat is
        not writing its log (consumers then disable roster rules)."""
        with self._lock:
            if time.time() - self._log_mtime > _STALE_SECONDS:
                return None
            return frozenset(self._names)

    def display_names(self) -> list[str]:
        with self._lock:
            return sorted(self._names.values())

    # ── internals ────────────────────────────────────────────────────────
    def _loop(self) -> None:
        cur_path: str | None = None
        fh = None
        while not self._stop.is_set():
            try:
                logs = glob.glob(os.path.join(_LOG_DIR, "output_log_*.txt"))
                newest = max(logs, key=os.path.getmtime) if logs else None
                if newest and newest != cur_path:
                    if fh is not None:
                        fh.close()
                    fh = open(newest, "r", encoding="utf-8", errors="ignore")
                    cur_path = newest
                    with self._lock:
                        self._names.clear()
                if fh is not None and cur_path is not None:
                    chunk = fh.read()
                    if chunk:
                        self._ingest(chunk)
                        with self._lock:
                            _n = len(self._names)
                        if _n != self._last_logged_count:
                            # visible heartbeat: proves the tail is alive and
                            # says what the matcher currently believes
                            self._last_logged_count = _n
                            logger.info("[VRCRoster] %d player(s) in room "
                                        "(log: %s)", _n,
                                        os.path.basename(cur_path))
                    # freshness comes from the FILE's mtime, not our read
                    # time — the initial full read of yesterday's log must
                    # not make a dead session look live
                    try:
                        m = os.path.getmtime(cur_path)
                        with self._lock:
                            self._log_mtime = m
                    except OSError:
                        pass
            except Exception:
                logger.debug("[VRCRoster] tail error", exc_info=True)
            self._stop.wait(1.0)

    def _ingest(self, chunk: str) -> None:
        for line in chunk.splitlines():
            if "OnPlayerJoined" in line:
                m = _JOIN_RE.search(line)
                if m:
                    name = m.group(1).strip()
                    if 0 < len(name) <= 64:
                        with self._lock:
                            self._names[norm_name(name)] = name
            elif "OnPlayerLeft" in line:
                m = _LEFT_RE.search(line)
                if m:
                    with self._lock:
                        self._names.pop(norm_name(m.group(1).strip()), None)
            elif "Joining" in line and _WORLD_RE.search(line):
                with self._lock:
                    self._names.clear()
