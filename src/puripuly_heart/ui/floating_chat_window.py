r"""Floating chat: a small always-on-top window with the latest chat lines and a
box to type a message.

Launched by the main app as `PuriPulyHeart.exe --floating-chat --port N
--token T --locale L` (or `-m puripuly_heart.main ...` from source). It holds no
translator or keys itself: lines arrive from the main app over a loopback
socket (ui/floating_chat_server.py) and typed text is sent back to be
translated and posted exactly like the main chat box. Closing the main app
closes this window.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

_PREFS_NAME = "floating_chat.json"
_DEFAULT_OPACITY = 0.92
_MAX_LINES = 80

_BG = "#1b1c1f"
_TEXT = "#e6e6e6"
_FAINT = "#8a8d93"
_SENT = "#4aa3df"
_RECV = "#48a495"


def _prefs_path():
    from puripuly_heart.config.paths import user_config_dir

    return user_config_dir() / _PREFS_NAME


def _load_prefs() -> dict:
    try:
        data = json.loads(_prefs_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_prefs(prefs: dict) -> None:
    with contextlib.suppress(Exception):
        _prefs_path().write_text(json.dumps(prefs), encoding="utf-8")


def parse_args(argv: list[str]) -> tuple[int, str, str]:
    def _value(flag: str, default: str = "") -> str:
        if flag in argv:
            idx = argv.index(flag)
            if idx + 1 < len(argv):
                return argv[idx + 1]
        return default

    return int(_value("--port", "0") or 0), _value("--token"), _value("--locale", "en")


def run_floating_chat_window(argv: list[str]) -> int:
    import flet as ft

    from puripuly_heart import boot_stealth as _bs
    from puripuly_heart.ui.fonts import font_for_language
    from puripuly_heart.ui.i18n import set_locale, t

    port, token, locale = parse_args(argv)
    if not port or not token:
        return 2
    set_locale(locale)
    _bs.start()

    def _main(page: ft.Page) -> None:
        prefs = _load_prefs()
        page.title = t("floating_chat.title", default="Chat")
        page.padding = 0
        page.bgcolor = _BG
        geom = prefs.get("geom") or {}
        for attr, val in (
            ("width", geom.get("w", 380)),
            ("height", geom.get("h", 520)),
            ("left", geom.get("x")),
            ("top", geom.get("y")),
            ("min_width", 260),
            ("min_height", 240),
        ):
            if val is not None:
                with contextlib.suppress(Exception):
                    setattr(page.window, attr, val)
        opacity = float(prefs.get("opacity", _DEFAULT_OPACITY))
        pinned = bool(prefs.get("pinned", True))
        with contextlib.suppress(Exception):
            page.window.always_on_top = pinned
            page.window.opacity = opacity

        state: dict = {"writer": None}
        font = font_for_language(locale)

        lines = ft.ListView(expand=True, spacing=6, auto_scroll=True, padding=10)
        status = ft.Text(t("floating_chat.connecting", default="Connecting..."),
                         size=11, color=_FAINT)

        def _line(entry: dict) -> ft.Control:
            peer = entry.get("channel") == "peer"
            label = t("dashboard.chat.received") if peer else t("dashboard.chat.sent")
            speaker = str(entry.get("speaker") or "")
            head = f"{label}{' - ' + speaker if speaker else ''}  {entry.get('time', '')}"
            translation = str(entry.get("translation") or "")
            source = str(entry.get("source") or "")
            rows: list[ft.Control] = [
                ft.Text(head, size=10, color=_RECV if peer else _SENT,
                        weight=ft.FontWeight.W_600),
            ]
            main_text = translation or source
            if main_text:
                rows.append(ft.Text(main_text, size=14, color=_TEXT, selectable=True,
                                    font_family=font))
            if translation and source and source.strip() != translation.strip():
                rows.append(ft.Text(source, size=11, color=_FAINT, selectable=True,
                                    font_family=font))
            return ft.Column(rows, spacing=1, tight=True)

        def _add(entry: dict) -> None:
            lines.controls.append(_line(entry))
            if len(lines.controls) > _MAX_LINES:
                del lines.controls[: len(lines.controls) - _MAX_LINES]

        entry_box = ft.TextField(
            hint_text=t("display.input_hint"),
            expand=True, multiline=True, min_lines=1, max_lines=3, shift_enter=True,
            text_size=13, color=_TEXT, border_radius=8, bgcolor="#26282c",
            border_color="#3a3d42", focused_border_color=_RECV,
            content_padding=ft.padding.symmetric(horizontal=10, vertical=6),
        )

        async def _send(_e=None) -> None:
            text = (entry_box.value or "").strip()
            writer = state.get("writer")
            if not text or writer is None:
                return
            entry_box.value = ""
            entry_box.update()
            with contextlib.suppress(Exception):
                writer.write((json.dumps({"type": "submit", "text": text},
                                         ensure_ascii=False) + "\n").encode("utf-8"))
                await writer.drain()

        entry_box.on_submit = lambda e: page.run_task(_send)

        def _set_opacity(e) -> None:
            value = max(0.3, min(1.0, float(e.control.value)))
            with contextlib.suppress(Exception):
                page.window.opacity = value
                page.update()
            prefs["opacity"] = value
            _save_prefs(prefs)

        def _toggle_pin(e) -> None:
            prefs["pinned"] = not bool(prefs.get("pinned", True))
            with contextlib.suppress(Exception):
                page.window.always_on_top = prefs["pinned"]
            pin_btn.icon = ft.Icons.PUSH_PIN if prefs["pinned"] else ft.Icons.PUSH_PIN_OUTLINED
            page.update()
            _save_prefs(prefs)

        pin_btn = ft.IconButton(
            ft.Icons.PUSH_PIN if pinned else ft.Icons.PUSH_PIN_OUTLINED,
            icon_size=16, icon_color=_FAINT,
            tooltip=t("floating_chat.pin", default="Keep on top"),
            on_click=_toggle_pin,
        )
        header = ft.Container(
            content=ft.Row(
                [
                    status,
                    ft.Container(expand=True),
                    ft.Icon(ft.Icons.OPACITY, size=14, color=_FAINT),
                    ft.Slider(min=0.3, max=1.0, value=opacity, width=110,
                              on_change_end=_set_opacity),
                    pin_btn,
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.only(left=10, right=4, top=2, bottom=2),
            bgcolor="#202226",
        )
        footer = ft.Container(
            content=ft.Row(
                [entry_box, ft.IconButton(ft.Icons.SEND_ROUNDED, icon_size=18,
                                          icon_color=_RECV,
                                          on_click=lambda e: page.run_task(_send))],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=8, vertical=6),
        )
        page.add(ft.Column([header, lines, footer], spacing=0, expand=True))

        def _save_geom(_e=None) -> None:
            with contextlib.suppress(Exception):
                if bool(getattr(page.window, "minimized", False)):
                    return
                prefs["geom"] = {"w": page.window.width, "h": page.window.height,
                                 "x": page.window.left, "y": page.window.top}
                _save_prefs(prefs)

        with contextlib.suppress(Exception):
            page.window.on_event = lambda e: _save_geom()

        async def _connect() -> None:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=1 << 20)
            except Exception:
                status.value = t("floating_chat.disconnected", default="Main window not running")
                page.update()
                return
            state["writer"] = writer
            writer.write((json.dumps({"type": "hello", "token": token}) + "\n").encode("utf-8"))
            await writer.drain()
            status.value = t("floating_chat.title", default="Chat")
            page.update()
            try:
                while True:
                    raw = await reader.readline()
                    if not raw:
                        break
                    try:
                        message = json.loads(raw.decode("utf-8"))
                    except Exception:
                        continue
                    if message.get("type") == "history":
                        for entry in message.get("entries") or []:
                            _add(entry)
                    elif message.get("type") == "entry":
                        _add(message)
                    else:
                        continue
                    lines.update()
            finally:
                state["writer"] = None
            # the main app went away: nothing left to show
            with contextlib.suppress(Exception):
                _save_geom()
                page.window.close()

        page.run_task(_connect)
        with contextlib.suppress(Exception):
            page.window.visible = True
            page.update()
            _bs.finish()

    ft.app(target=_main, view=ft.AppView.FLET_APP_HIDDEN)
    return 0
