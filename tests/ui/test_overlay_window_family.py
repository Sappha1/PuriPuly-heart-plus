"""r385: the always-on-top guard never found its window, in any build.

r381 replaced a title lookup (FindWindowW, which returns 0 for this window) with
"enumerate the windows THIS process owns", and added a warning when that failed.
The warning is the only reason this is provable: across every session since, the
log has zero "window handle resolved" lines and a "could not resolve this
process's window" on every launch.

The Python process does not own the window. It runs the flet server and spawns
flet.exe, and that CHILD hosts the Flutter window — the log says so four lines
above the warning:

    flet_desktop: Flet View found in: ...\\flet_desktop\\app\\flet\\flet.exe
    flet_desktop: Starting Flet View app...

So a same-pid test can never match, and the guard has been inert since it was
written. The window belongs to a descendant, so the resolver has to ask for
descendants.

The parent walk is pure over (pid, parent_pid) pairs precisely so it can be tested
here — no Windows, no process tree, no GUI.
"""
from __future__ import annotations

from pathlib import Path

from puripuly_heart.ui.desktop_overlay import process_family

SOURCE = Path("src/puripuly_heart/ui/desktop_overlay.py")


def test_a_child_process_is_in_the_family() -> None:
    """The whole point: flet.exe is a child, and its window must be reachable."""
    assert process_family(100, [(100, 1), (200, 100)]) == {100, 200}


def test_a_grandchild_is_in_the_family() -> None:
    assert process_family(100, [(100, 1), (200, 100), (300, 200)]) == {100, 200, 300}


def test_unrelated_processes_are_excluded() -> None:
    """Both the dashboard and the overlay run a flet.exe with the same window
    class, so picking up somebody else's window would re-point the guard at the
    wrong window entirely."""
    family = process_family(100, [(100, 1), (200, 100), (900, 1), (901, 900)])
    assert family == {100, 200}
    assert 900 not in family and 901 not in family


def test_the_root_is_always_included() -> None:
    assert process_family(100, []) == {100}


def test_a_self_parented_process_does_not_hang() -> None:
    """Windows recycles pids, so a process table can name a process its own
    parent. A naive walk loops forever and the overlay never starts."""
    assert process_family(5, [(5, 5), (6, 5)]) == {5, 6}


def test_a_cyclic_process_table_does_not_hang() -> None:
    """Same hazard one step removed: a recycled pid can make two entries each
    other's ancestor."""
    assert process_family(1, [(1, 2), (2, 1), (3, 1)]) == {1, 2, 3}


def test_the_resolver_no_longer_filters_on_own_pid_alone() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("def _resolve_native_hwnd")
    body = text[start : text.index("def _reassert_native_topmost", start)]
    assert "pid.value == own_pid" not in body, (
        "the window search is back to this process's own pid; the Flutter window "
        "belongs to the flet.exe child, so that matches nothing and the "
        "always-on-top guard silently does nothing again"
    )
    assert "_own_process_family" in body
    assert "GetClassNameW" in body, "the Flutter window class is no longer preferred"


def test_the_snapshot_handle_is_declared_as_a_handle() -> None:
    """Without an explicit HANDLE restype ctypes truncates the snapshot handle to
    32 bits, which yields an unusable handle once the value is large enough — a
    machine-dependent failure that would put this straight back to resolving
    nothing, with the same silent symptom."""
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("def _own_process_family")
    body = text[start : start + 2600]
    assert "CreateToolhelp32Snapshot.restype = wintypes.HANDLE" in body
    assert "Process32First.argtypes" in body and "Process32Next.argtypes" in body


def test_failure_to_resolve_is_still_reported() -> None:
    """The r381 warning is what made this diagnosable at all — a guard that runs
    but never finds its window looks exactly like one that works."""
    text = SOURCE.read_text(encoding="utf-8")
    assert "could not resolve" in text
