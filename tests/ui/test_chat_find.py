"""r371: Ctrl+F find-in-chat.

Asked for as "the find dialogue like every other program has". The part worth
testing is not that it finds things — it is that highlighting a message is
strictly reversible. Highlighting swaps a Text's `value` for a list of spans,
so a bug there does not look like a broken search, it looks like the chat log
losing its own text.
"""
from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from puripuly_heart.ui.app import TranslatorApp
from puripuly_heart.ui.views.dashboard import DashboardView


def _entry(*lines: str) -> ft.Container:
    """A chat entry shaped like the real one: Container > Column > Text rows."""
    return ft.Container(
        content=ft.Column([ft.Text(line, size=12) for line in lines], spacing=1),
        padding=ft.padding.all(4),
    )


def _dashboard(*entries: ft.Container) -> DashboardView:
    dash = DashboardView.__new__(DashboardView)
    dash._chat_list_view = ft.Column(controls=list(entries))
    dash._chat_entry_seq = 0
    dash._find_visible = False
    dash._find_query = ""
    dash._find_matches = []
    dash._find_index = -1
    dash._find_originals = {}
    dash._find_count = ft.Text("")
    dash._find_bar = ft.Container(visible=False)
    dash._find_field = ft.TextField()
    for index, entry in enumerate(entries, start=1):
        entry.key = f"chatentry-{index}"
    return dash


def _rendered(entry: ft.Container) -> list[str]:
    """What each Text in the entry would draw — value, or its spans joined."""
    out = []
    for text in DashboardView._find_walk_texts(entry):
        if text.spans:
            out.append("".join(span.text for span in text.spans))
        else:
            out.append(text.value or "")
    return out


def test_it_finds_every_occurrence_across_messages() -> None:
    a = _entry("hello there", "nothing here")
    b = _entry("well hello again", "hello hello")
    dash = _dashboard(a, b)

    dash._run_find("hello")

    assert len(dash._find_matches) == 4, [m[2:] for m in dash._find_matches]
    assert dash._find_count.value == "4/4"


def test_the_search_is_case_insensitive() -> None:
    dash = _dashboard(_entry("Hello", "HELLO", "hello"))
    dash._run_find("hello")
    assert len(dash._find_matches) == 3


def test_highlighting_never_changes_the_text_that_is_drawn() -> None:
    """The spans must join back to the original, character for character."""
    lines = ["hello there", "a hello, and hello."]
    entry = _entry(*lines)
    dash = _dashboard(entry)

    dash._run_find("hello")

    assert _rendered(entry) == lines, "highlighting altered the message text"


def test_closing_restores_the_original_values_exactly() -> None:
    lines = ["hello there", "nothing", "hello again"]
    entry = _entry(*lines)
    dash = _dashboard(entry)
    dash._find_visible = True

    dash._run_find("hello")
    assert any(text.spans for text in DashboardView._find_walk_texts(entry))

    assert dash.close_find_bar() is True

    for text, expected in zip(DashboardView._find_walk_texts(entry), lines):
        assert text.value == expected, "a message did not come back"
        assert not text.spans, "spans were left behind on a closed find"
    assert dash._find_originals == {}
    assert dash._find_matches == []


def test_an_empty_query_clears_everything() -> None:
    lines = ["hello there"]
    entry = _entry(*lines)
    dash = _dashboard(entry)

    dash._run_find("hello")
    dash._run_find("")

    assert dash._find_matches == []
    assert dash._find_count.value == ""
    for text, expected in zip(DashboardView._find_walk_texts(entry), lines):
        assert text.value == expected


def test_it_starts_on_the_newest_match_and_wraps_both_ways() -> None:
    """A chat is read from the bottom; the first match is usually far above
    whatever the user is actually looking at."""
    dash = _dashboard(_entry("hello one"), _entry("hello two"), _entry("hello three"))

    dash._run_find("hello")
    assert dash._find_index == 2, "did not start on the newest hit"
    assert dash._find_count.value == "3/3"

    dash.find_next()
    assert dash._find_index == 0, "next did not wrap to the top"
    dash.find_prev()
    assert dash._find_index == 2, "prev did not wrap to the bottom"


def test_only_the_current_match_is_drawn_as_current() -> None:
    dash = _dashboard(_entry("hello hello"))
    dash._run_find("hello")

    from puripuly_heart.ui.views import dashboard as module

    spans = [
        span
        for text in DashboardView._find_walk_texts(dash._chat_list_view.controls[0])
        for span in (text.spans or [])
        if span.style is not None and span.style.bgcolor
    ]
    current = [s for s in spans if s.style.bgcolor == module._FIND_CURRENT_BG]
    other = [s for s in spans if s.style.bgcolor == module._FIND_MATCH_BG]

    assert len(current) == 1, "more than one hit is drawn as the current one"
    assert len(other) == 1
    assert current[0].style.color == module._FIND_CURRENT_FG, (
        "the opaque current highlight has no readable foreground"
    )


def test_a_query_whose_case_folding_changes_length_does_not_misalign() -> None:
    """'İ'.lower() is TWO characters. Offsets taken from the lowered string
    would slice the original in the wrong place — this app is full of other
    people's alphabets, so that has to be handled rather than hoped about."""
    original = "İstanbul plan"
    entry = _entry(original)
    dash = _dashboard(entry)

    dash._run_find("plan")

    assert dash._find_matches, "nothing found in a string that lowers longer"
    _entry_ctrl, text, start, end = dash._find_matches[0]
    assert original[start:end] == "plan", (
        f"offsets addressed {original[start:end]!r}, not the match"
    )
    assert _rendered(entry) == [original]


def test_no_match_reports_it_rather_than_showing_a_stale_count() -> None:
    dash = _dashboard(_entry("hello"))
    dash._run_find("hello")
    assert dash._find_count.value == "1/1"

    dash._run_find("zzzz")
    assert dash._find_matches == []
    assert dash._find_count.value not in ("", "1/1")


def test_entries_carry_a_key_so_a_match_can_be_scrolled_to() -> None:
    """Without a key on the entry there is no way to bring the hit on screen,
    and find silently becomes 'highlight something off-screen'."""
    import re
    from pathlib import Path

    source = Path("src/puripuly_heart/ui/views/dashboard.py").read_text(encoding="utf-8")
    appends = source.count("self._chat_list_view.controls.append(entry)")
    keyed = len(re.findall(r'entry\.key = f"chatentry-\{self\._chat_entry_seq\}"', source))
    assert appends >= 2, "chat entry creation sites moved"
    assert keyed == appends, (
        f"{appends} places append a chat entry but only {keyed} give it a key"
    )


# ── the shortcut itself ──────────────────────────────────────────────────


def _key_event(key: str, *, ctrl: bool = False, shift: bool = False, alt: bool = False):
    return SimpleNamespace(key=key, ctrl=ctrl, shift=shift, alt=alt, meta=False)


def _app_with_dashboard():
    """Built with the app's REAL nesting, not a shape invented to satisfy the
    guard.

    r372: the first version of this helper set `content_area = SimpleNamespace(
    content=dash)`, copied from the existing Tab test. The app actually puts the
    view in `_inner_content.content`, with `content_area.content` being the
    Column that holds [top nav bar, view container]. Faking the shape the guard
    expected made fifteen tests pass while Ctrl+F did nothing at all in the app.
    """
    app = TranslatorApp.__new__(TranslatorApp)
    calls: list[str] = []
    dash = SimpleNamespace(
        open_find_bar=lambda: calls.append("open"),
        close_find_bar=lambda: (calls.append("close") or True),
        handle_message_input_tab_key=lambda: calls.append("tab"),
    )
    app.view_dashboard = dash
    app._inner_content = ft.Container(content=dash)
    top_nav = ft.Container()
    app.content_area = ft.Container(
        content=ft.Column([top_nav, app._inner_content])
    )
    return app, calls


def test_the_guard_reads_the_container_that_view_switching_writes() -> None:
    """The bug that made r371 do nothing, in one assertion.

    The shortcut guard and the code that switches views must agree on where the
    active view lives. They did not: switching assigns `_inner_content.content`
    while the guard compared `content_area.content`, which is the Column holding
    the nav bar — never the dashboard. Both shortcuts were dead and the suite was
    green, because the fake matched the guard instead of the app.
    """
    import re
    from pathlib import Path

    source = Path("src/puripuly_heart/ui/app.py").read_text(encoding="utf-8")

    written = set(re.findall(r"self\.(_\w+)\.content = ", source))
    assert written, "nothing assigns a view container any more — layout moved"

    start = source.index("def _dashboard_is_active")
    guard = source[start : source.index("def _on_keyboard_event", start)]
    read = set(re.findall(r'getattr\(self, "(_\w+)", None\)', guard))

    assert written & read, (
        f"view switching writes {sorted(written)} but the shortcut guard reads "
        f"{sorted(read)} — the guard can never be true, and every keyboard "
        f"shortcut silently does nothing"
    )


def test_ctrl_f_opens_the_find_bar() -> None:
    app, calls = _app_with_dashboard()
    app._on_keyboard_event(_key_event("F", ctrl=True))
    assert calls == ["open"]


def test_plain_f_is_left_alone_so_typing_still_works() -> None:
    app, calls = _app_with_dashboard()
    app._on_keyboard_event(_key_event("F"))
    assert calls == [], "typing an f opened the find bar"


def test_escape_closes_it() -> None:
    app, calls = _app_with_dashboard()
    app._on_keyboard_event(_key_event("Escape"))
    assert calls == ["close"]


def test_tab_still_swaps_languages() -> None:
    """The shortcut this handler already had must survive being shared."""
    app, calls = _app_with_dashboard()
    app._on_keyboard_event(_key_event("Tab"))
    assert calls == ["tab"]


def test_the_shortcut_does_nothing_outside_the_dashboard() -> None:
    """Settings and dialogs get their own Ctrl+F behaviour, or none."""
    app, calls = _app_with_dashboard()
    # Switch views the way the app does: assign the inner container, which is
    # exactly what _select_nav_index writes.
    app._inner_content.content = ft.Container()
    app._on_keyboard_event(_key_event("F", ctrl=True))
    app._on_keyboard_event(_key_event("Escape"))
    app._on_keyboard_event(_key_event("Tab"))
    assert calls == []


def test_the_find_bar_floats_instead_of_pushing_the_messages_down() -> None:
    """r373: asked for Chrome's shape — a small panel pinned top-right that
    hovers over the content.

    As a child of the chat box Column it took vertical space, so opening and
    closing it reflowed every message. It has to live in the Stack that already
    holds the message list, which is what makes it an overlay.
    """
    from pathlib import Path

    source = Path("src/puripuly_heart/ui/views/dashboard.py").read_text(encoding="utf-8")

    assert "self._notice_strip,\n                    self._find_bar," not in source, (
        "the find bar is back in the chat box Column — it will push the "
        "messages down every time it opens"
    )
    assert "content=self._find_bar," in source, (
        "the find bar is not mounted in the Stack, so it will not render"
    )

    stack_at = source.index("content=ft.Stack(")
    bar_at = source.index("content=self._find_bar,")
    jump_at = source.index("content=self._chat_jump_btn,")
    assert stack_at < bar_at, "the find bar is mounted before the Stack it belongs to"
    assert jump_at < bar_at, (
        "the find bar is stacked below the jump button; later children draw on "
        "top, and it must be the topmost thing in the chat box"
    )
