import asyncio
import contextlib
import datetime
import logging
import time
from typing import Any, Callable

import flet as ft

logger = logging.getLogger(__name__)

from puripuly_heart.core.language import get_all_language_options, is_local_qwen_supported
from puripuly_heart.core.transliteration import transliterate_for_language
from puripuly_heart.ui.components.display_card import DisplayCard
from puripuly_heart.ui.components.language_modal import LanguageModal
from puripuly_heart.ui.components.settings import OptionItem, SettingsModal
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, language_name, t
from puripuly_heart.ui.overlay_peer_contract import OverlayPeerConsumerContract

_BUILD_TAG = "r282"  #increment each build so user can confirm version

# ── VRCT-style dark palette ──────────────────────────────────────────────────
_BG_MAIN = "#2e2f32"
_BG_SIDEBAR = "#3a3b3e"
_BG_CHAT = "#292a2d"
_BG_INPUT = "#323336"
_BG_ROW_HOVER = "#4b4c4f"
_BG_ROW_DEFAULT = "#434447"
_BORDER_INPUT = "#5b5c5f"
_TEXT_PRIMARY = "#f2f2f2"
_TEXT_MUTED = "#a9aaae"
_TEXT_FAINT = "#7f8084"
_TOGGLE_ON = "#48a495"
_TOGGLE_OFF = "#535457"
_TOGGLE_ON_HOVER = "#55ac9e"
_TOGGLE_WARNING = "#cf7b1b"
_TOGGLE_ERROR = "#e03030"
_SENT_COLOR = "#6197b4"
_RECV_COLOR = "#a861b4"
_DIVIDER = "#4b4c4f"
_SCROLLBAR = "#4b4c4f"

CHAT_MAX_ENTRIES = 200
OVERLAY_FAILURE_REASON_ONLY_NOTICE_REASONS = {"steamvr_not_running"}


class _ToggleRow(ft.Container):
    """VRCT-style horizontal toggle row: icon + label + pill indicator."""

    def __init__(self, icon: str, label: str, *, on_click):
        self._label_text = ft.Text(label, size=14, color=_TEXT_PRIMARY)
        # Small caption under the label showing the active model (e.g. "Qwen ASR 0.6B"),
        # so it's obvious at a glance which STT model is in use without right-clicking.
        self._sublabel_text = ft.Text("", size=9, color=_TEXT_FAINT, visible=False, no_wrap=True)
        label_col = ft.Column(
            [self._label_text, self._sublabel_text],
            spacing=0,
            tight=True,
            expand=True,
        )
        self._dot = ft.Container(width=10, height=10, border_radius=5, bgcolor=_TOGGLE_OFF)
        self._spinner = ft.ProgressRing(
            width=12, height=12, stroke_width=1.5, color=_TOGGLE_WARNING, visible=False
        )
        self._indicator = ft.Stack(
            [self._dot, self._spinner],
            width=12,
            height=12,
        )

        super().__init__(
            content=ft.Row(
                [
                    ft.Icon(icon, size=18, color=_TEXT_MUTED),
                    label_col,
                    self._indicator,
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=16, vertical=7),
            bgcolor=_BG_SIDEBAR,
            border_radius=6,
            on_click=on_click,
            on_hover=self._on_hover,
            ink=False,
        )
        self._state = False
        self._warning = False
        self._loading = False
        self._loading_since = 0.0

    def _on_hover(self, e):
        self.bgcolor = _BG_ROW_HOVER if e.data == "true" else _BG_SIDEBAR
        self.update()

    def set_loading(self, loading: bool, progress: float | None = None) -> None:
        """Show the loading ring. progress=None spins indeterminately; 0.0-1.0 renders a
        determinate ring that fills up (used while the speech model downloads/loads)."""
        if loading and not self._loading:
            self._loading_since = time.monotonic()
        self._loading = loading
        self._spinner.value = (
            None if progress is None else max(0.02, min(1.0, float(progress)))
        )
        self._spinner.visible = loading
        self._dot.visible = not loading
        try:
            self._indicator.update()
        except Exception:
            pass

    @property
    def is_loading(self) -> bool:
        return bool(self._loading)

    def set_state(self, on: bool, *, warning: bool = False, error: bool = False):
        # While the row is mid-load and only a WARNING (becoming-ready) state lands,
        # keep the progress ring on screen instead of flashing an orange dot between
        # the filled ring and the green light. Failsafe: after 20s of loading, fall
        # back to the normal dot so a stuck warning is still visible.
        if (
            warning
            and not error
            and self._loading
            and (time.monotonic() - getattr(self, "_loading_since", 0.0)) < 20.0
        ):
            self._state = on
            self._warning = True
            return
        self._state = on
        self._warning = warning
        self._loading = False
        self._spinner.visible = False
        self._dot.visible = True
        if error:
            self._dot.bgcolor = _TOGGLE_ERROR
        elif warning:
            self._dot.bgcolor = _TOGGLE_WARNING
        elif on:
            self._dot.bgcolor = _TOGGLE_ON
        else:
            self._dot.bgcolor = _TOGGLE_OFF
        try:
            self._indicator.update()
        except Exception:
            try:
                self._dot.update()
            except Exception:
                pass

    def set_label(self, label: str):
        self._label_text.value = label
        try:
            self._label_text.update()
        except Exception:
            pass

    def set_sublabel(self, text: str):
        self._sublabel_text.value = text or ""
        self._sublabel_text.visible = bool(text)
        try:
            self._sublabel_text.update()
        except Exception:
            pass

    def set_tooltip(self, text: str):
        self.tooltip = text
        try:
            self.update()
        except Exception:
            pass


class _LangRow(ft.Container):
    """Compact language pair row: label | [src] → [tgt]"""

    _BTN_STYLE = ft.ButtonStyle(
        color={ft.ControlState.DEFAULT: _TEXT_PRIMARY, ft.ControlState.HOVERED: _TOGGLE_ON},
        padding=ft.padding.symmetric(horizontal=4, vertical=0),
        overlay_color=ft.Colors.TRANSPARENT,
        text_style=ft.TextStyle(size=12),
    )

    def __init__(self, label: str, src: str, tgt: str, *, on_src, on_tgt, on_swap):
        self._src_btn = ft.TextButton(src, on_click=on_src, style=self._BTN_STYLE)
        self._tgt_btn = ft.TextButton(tgt, on_click=on_tgt, style=self._BTN_STYLE)
        self._swap_btn = ft.Container(
            content=ft.Icon(ft.Icons.SWAP_HORIZ, size=14, color=_TEXT_FAINT),
            on_click=on_swap,
            tooltip=t("dashboard.tooltip.swap_languages"),
            padding=ft.padding.symmetric(horizontal=2, vertical=0),
            border_radius=3,
        )
        super().__init__(
            content=ft.Column(
                [
                    ft.Text(label, size=10, color=_TEXT_FAINT, text_align=ft.TextAlign.CENTER),
                    ft.Row(
                        [
                            self._src_btn,
                            self._swap_btn,
                            self._tgt_btn,
                        ],
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                spacing=0,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=12, vertical=6),
        )

    def set_languages(self, src: str, tgt: str):
        self._src_btn.text = src
        self._tgt_btn.text = tgt
        try:
            self._src_btn.update()
            self._tgt_btn.update()
        except Exception:
            pass


class _MiniIconBtn(ft.Container):
    """Compact icon + state-dot button for collapsed sidebar."""

    def __init__(self, icon: str, tooltip: str, *, on_click):
        self._icon = ft.Icon(icon, size=20, color=_TEXT_MUTED)
        self._dot = ft.Container(width=7, height=7, border_radius=3.5, bgcolor=_TOGGLE_OFF)
        super().__init__(
            content=ft.Stack(
                [
                    ft.Container(content=self._icon, alignment=ft.alignment.center, expand=True),
                    ft.Container(
                        content=self._dot,
                        alignment=ft.alignment.bottom_right,
                        padding=ft.padding.only(right=7, bottom=7),
                    ),
                ],
                width=44,
                height=40,
            ),
            width=44,
            height=40,
            border_radius=6,
            bgcolor=ft.Colors.TRANSPARENT,
            tooltip=tooltip,
            on_click=on_click,
            on_hover=self._on_hover,
        )

    def _on_hover(self, e):
        self.bgcolor = _BG_ROW_HOVER if e.data == "true" else ft.Colors.TRANSPARENT
        try:
            self.update()
        except Exception:
            pass

    def set_state(self, on: bool, *, warning: bool = False, error: bool = False):
        if error:
            self._dot.bgcolor = _TOGGLE_ERROR
            self._icon.color = _TOGGLE_ERROR
        elif warning:
            self._dot.bgcolor = _TOGGLE_WARNING
            self._icon.color = _TOGGLE_WARNING
        elif on:
            self._dot.bgcolor = _TOGGLE_ON
            self._icon.color = _TOGGLE_ON
        else:
            self._dot.bgcolor = _TOGGLE_OFF
            self._icon.color = _TEXT_MUTED
        try:
            self._dot.update()
            self._icon.update()
        except Exception:
            pass

    def set_tooltip(self, text: str) -> None:
        self.tooltip = text
        try:
            self.update()
        except Exception:
            pass


class _UpdateNavBtn(ft.Container):
    """Sidebar self-update button. Not rendered at all while up to date; morphs
    in place through the whole flow: teal download icon (available) → filling
    ring (downloading) → solid teal restart icon (staged, ready)."""

    def __init__(self, *, on_click):
        # Sized to match the settings gear button exactly (44x40 slot) so the
        # bottom nav row reads as one consistent set of controls.
        # Visual mass matches the bare 20px settings gear glyph next to it: a
        # 26px box/ring, not the 32px it used to be (which read oversized).
        self._icon = ft.Icon(ft.Icons.DOWNLOAD, size=16, color=_TOGGLE_ON)
        self._ring = ft.ProgressRing(
            value=0, width=26, height=26, stroke_width=2,
            color=_TOGGLE_WARNING, bgcolor="#3f4044", visible=False,
        )
        self._inner = ft.Container(
            content=self._icon, width=26, height=26, border_radius=6,
            alignment=ft.alignment.center,
            bgcolor="#2e4a45", border=ft.border.all(1, _TOGGLE_ON),
        )
        self._on_click_cb = on_click
        self._active = False
        super().__init__(
            content=ft.Stack(
                [
                    ft.Container(content=self._ring, alignment=ft.alignment.center, expand=True),
                    ft.Container(content=self._inner, alignment=ft.alignment.center, expand=True),
                ],
                width=44, height=40,
            ),
            width=44, height=40,
            border_radius=6,
            bgcolor=ft.Colors.TRANSPARENT,
            # The 44x40 slot is ALWAYS laid out; hiding is done via opacity so
            # the gear next to it never shifts when the button appears.
            visible=True,
            opacity=0.0,
            disabled=True,
            on_click=self._handle_click,
            on_hover=self._on_hover,
        )

    def _handle_click(self, e) -> None:
        if self._active and callable(self._on_click_cb):
            self._on_click_cb(e)

    def _on_hover(self, e):
        if not self._active:
            return
        self.bgcolor = _BG_ROW_HOVER if e.data == "true" else ft.Colors.TRANSPARENT
        try:
            self.update()
        except Exception:
            pass

    def set_flow(self, state: str, progress: float, visible: bool, tooltip: str) -> None:
        self._active = bool(visible)
        self.opacity = 1.0 if visible else 0.0
        self.disabled = not visible
        self.tooltip = (tooltip or None) if visible else None
        if not visible:
            self.bgcolor = ft.Colors.TRANSPARENT
        if state == "downloading":
            self._ring.visible = True
            # Floor keeps the arc visible from the first pixel of progress.
            self._ring.value = max(0.02, min(1.0, progress))
            self._inner.bgcolor = ft.Colors.TRANSPARENT
            self._inner.border = None
            self._icon.name = ft.Icons.ARROW_DOWNWARD
            self._icon.size = 12
            self._icon.color = _TOGGLE_WARNING
        elif state in ("ready", "restarting"):
            self._ring.visible = False
            self._inner.bgcolor = _TOGGLE_ON
            self._inner.border = None
            self._icon.name = ft.Icons.RESTART_ALT
            self._icon.size = 16
            self._icon.color = "#10322d"
        else:  # available (any hidden state renders the same defaults)
            self._ring.visible = False
            self._inner.bgcolor = "#2e4a45"
            self._inner.border = ft.border.all(1, _TOGGLE_ON)
            self._icon.name = ft.Icons.DOWNLOAD
            self._icon.size = 16
            self._icon.color = _TOGGLE_ON
        try:
            if self.page:
                self.update()
        except Exception:
            pass


class DashboardView(ft.Row):
    """VRCT-style dashboard: dark sidebar on left, chat panel on right."""

    _LANG_OPTIONS = get_all_language_options()

    def __init__(self):
        super().__init__(expand=True, spacing=0)

        # State
        self._sidebar_collapsed = False
        self.is_connected = False
        self.is_power_on = False
        self.is_translation_on = True
        self.is_stt_on = False
        self.show_pinyin = False
        self.show_romaji = False
        self.send_romaji = False
        self.send_pinyin = False
        self.show_latin = False
        self.send_latin = False
        # Pinyin grouped into words (péngyǒu) vs per-syllable (péng yǒu). Default grouped.
        self._pinyin_word_grouping = True
        self.on_pinyin_word_grouping_change: object = None  # callback(value: bool)
        # "Auto detect voice": incoming peer voice language auto-detected
        # instead of assumed to be the Target language (options menu pill).
        self._auto_detect_voice = False
        self.on_auto_detect_voice_change: object = None  # callback(value: bool)
        # "Separate text translation" pill (same options menu) — mirrors the
        # Settings row; ON = separate Text Translation box (unified OFF).
        self.on_separate_text_translation_change: object = None  # callback(value: bool)
        # Last target picked while separate mode was ON — the unified mirror
        # overwrites target, so this restores the pick when switching back.
        self._separate_target_pref = ""
        self.on_separate_target_pref_change: object = None  # callback(code: str)
        # Chatbox text format state (mirrors osc.chatbox_include_source + ui.chatbox_reading_only).
        self._chatbox_include_source = True
        self._chatbox_reading_only = False
        self.on_chatbox_format_change: object = None  # callback(fmt_id: str)
        # In-app chat LOG format — independent of the chatbox format, so
        # "send translation only in game, but log everything" works.
        self._chat_log_format = "orig_read_trans"
        self.on_chat_log_format_change: object = None  # callback(fmt_id: str)
        self.translation_needs_key = False
        self.stt_needs_key = False
        self.last_sent_text = t("dashboard.ready")
        self.history_items = []
        self._chat_entries: list[ft.Control] = []
        self._chat_list_view: ft.ListView | None = None
        self._single_turn_mode_backing: bool = True

        self._pending_sent_col: ft.Column | None = None
        self._pending_version: int = 0
        self._show_pending_echo: bool = True  # on by default; toggled in Settings
        self._chatbox_send_peer: bool = False  # toggled in Settings and dashboard header
        self._loopback_selected_only: bool = False  # loop back only selected peer languages
        self._loopback_translation_only: bool = False  # loop back only the final translation
        self._self_in_overlay: bool = True  # show spoken messages on overlay
        self._typed_in_overlay: bool = True  # show typed messages on overlay
        self._stt_input_device: str = ""  # active mic device name for tooltip
        self._vrc_mute_sync: bool = False  # VRChat mute sync gate
        self._vrc_mute_sync_osc_state: bool | None = None  # None=not yet synced, True=VRC muted, False=VRC unmuted
        self._translation_showing_warning = False
        self._stt_showing_warning = False
        self._stt_showing_error = False
        self._peer_showing_error = False
        self._managed_auth_pending = False
        self._local_stt_notice_status: str | None = None
        self._local_stt_notice_percent: int | None = None
        self._overlay_peer_contract: OverlayPeerConsumerContract | None = None

        self._source_lang_code = "ko"
        self._target_lang_code = "en"
        self._extra_target_lang_codes: list[str] = []  # extra target languages (unlimited)
        self._peer_source_lang_code = ""  # empty = auto-detect; user sets via "Peer voice" card
        self._peer_target_lang_code = ""
        self._extra_peer_source_lang_codes: list[str] = []  # extra peer source languages (e.g. listen to JP + ZH)
        self._extra_peer_target_lang_codes: list[str] = []  # extra peer target languages
        self._extra_tgt_translit_cols: list[ft.Column] = []
        self._extra_peer_tgt_translit_cols: list[ft.Column] = []
        self._alt_source_lang_code: str | None = None  # second "you speak" language (None = hidden)
        self._active_preset: int = 0
        self._preset_data: list[dict] = [
            {"source": "en", "targets": ["zh-CN"]},
            {"source": "en", "targets": ["ja"]},
            {"source": "en", "targets": ["ko"]},
        ]
        self._message_input_focused = False
        self._last_chat_content_col: ft.Column | None = None
        self._filter_peer_lang_active: bool = True  # default ON: only show peer messages in configured language

        self._recent_source_langs: list[str] = []
        self._recent_target_langs: list[str] = []

        # Callbacks
        self.on_send_message = None
        self.on_toggle_translation = None
        self.on_toggle_stt = None
        self.on_toggle_overlay = None
        self.on_toggle_peer_translation = None
        self.on_toggle_ocr = None  # (prototype) callback(enabled: bool)
        self.on_ocr_remove_module = None  # callback() — remove-module confirm flow
        self.on_ocr_prewarm_change = None  # (prototype) callback(bool)
        self.on_ocr_region_toggle = None  # (prototype) callback()
        self.on_ocr_region_state = None  # (prototype) -> bool (region set?)
        self.on_ocr_bubbles_change = None  # (prototype) callback(bool)
        self.on_ocr_scope_change = None  # (prototype) callback(bool)
        self._ocr_on = False
        # OCR menu preferences persist in the overlay config file (shared
        # with the manager, which reads the same keys for launch args).
        try:
            from puripuly_heart.ocr.manager import load_ocr_prefs

            _ocr_p = load_ocr_prefs()
        except Exception:
            _ocr_p = {}
        # Unified translation view: hide the separate "Text Translation" card
        # and make typed messages mirror the Voice "Translate from" (partner's)
        # language. Read here for the initial card layout; the Settings toggle
        # applies live via set_unified_translation().
        self._unified_translation = True
        try:
            import json as _json
            import os as _os
            _sp = _os.path.join(_os.path.expanduser("~"), "AppData", "Local",
                                "puripuly-heart", "settings.json")
            with open(_sp, encoding="utf-8") as _fh:
                _sd = _json.load(_fh)
            self._unified_translation = bool(
                _sd.get("ui", {}).get("unified_translation_ui", True))
            self._auto_detect_voice = bool(
                _sd.get("languages", {}).get("auto_detect_peer_voice", False))
            self._separate_target_pref = str(
                _sd.get("languages", {}).get("separate_target_language", ""))
            self._chat_log_format = str(
                _sd.get("ui", {}).get("chat_log_format", "orig_read_trans"))
        except Exception:
            pass
        self._ocr_prewarm = bool(_ocr_p.get("prewarm", True))
        self._ocr_bubbles_only = bool(_ocr_p.get("bubbles_only", True))
        self._ocr_vrchat_only = bool(_ocr_p.get("vrchat_only", True))
        _wt = _ocr_p.get("window_title")
        self._ocr_window_title = (str(_wt) if _wt is not None
                                  else ("VRChat" if self._ocr_vrchat_only
                                        else ""))
        self.on_ocr_window_change = None  # (prototype) callback(title)
        self.on_ocr_window_list = None  # (prototype) -> list[str]
        self._ocr_foreign_only = bool(_ocr_p.get("foreign_only", True))
        self._ocr_ignore_names = bool(_ocr_p.get("ignore_names", True))
        self._ocr_ignore_pronouns = bool(_ocr_p.get("ignore_pronouns", True))
        self.on_ocr_ignore_pronouns_change = None  # (prototype) callback
        self._ocr_translate = bool(_ocr_p.get("translate", True))
        self.on_ocr_translate_change = None  # (prototype) callback(bool)
        self._ocr_xlat_service = str(_ocr_p.get("xlat_service", "bing"))
        self.on_ocr_xlat_service_change = None  # (prototype) callback(str)
        # Style/behavior prefs surfaced in the OCR menu (persisted, live).
        self._ocr_style = {
            "ocr_format": str(_ocr_p.get("ocr_format", "orig_pinyin_trans")),
            "ocr_place": str(_ocr_p.get("ocr_place", "cover")),
            "ocr_outline": str(_ocr_p.get("ocr_outline", "#ff2020")),
            "ocr_bg": str(_ocr_p.get("ocr_bg", "#14161a")),
            "ocr_bg_alpha": str(_ocr_p.get("ocr_bg_alpha", 100)),
            "ocr_text": str(_ocr_p.get("ocr_text", "auto")),
            "scan_mode": str(_ocr_p.get("scan_mode", "hold")),
            "scan_bind": str(_ocr_p.get("scan_bind", "E")),
            # ALT+E toggle out of the box — but only for FRESH configs; a
            # legacy config that set up a hold bind must not gain a
            # surprise toggle it never chose.
            "scan_bind_toggle": str(_ocr_p.get(
                "scan_bind_toggle",
                "ALT+E" if "scan_bind" not in _ocr_p else "")),
            "ocr_region_border": str(_ocr_p.get("ocr_region_border", 0)),
            "ocr_font_px": str(_ocr_p.get("ocr_font_px", 50)),
            "ocr_size_pinyin": str(_ocr_p.get("ocr_size_pinyin", 0)),
            "ocr_size_trans": str(_ocr_p.get("ocr_size_trans", 0)),
            "ocr_size_pronoun": str(_ocr_p.get("ocr_size_pronoun", 0)),
            "ocr_color_orig": str(_ocr_p.get("ocr_color_orig", "")),
            "ocr_color_trans": str(_ocr_p.get("ocr_color_trans", "")),
            "ocr_color_pinyin": str(_ocr_p.get("ocr_color_pinyin", "")),
            "ocr_color_pronoun": str(_ocr_p.get("ocr_color_pronoun", "")),
            "ocr_pinyin_tone": str(_ocr_p.get("ocr_pinyin_tone", 1)),
            "ocr_pinyin_group": str(_ocr_p.get("ocr_pinyin_group", 1)),
            "ignore_groups": str(_ocr_p.get("ignore_groups", 1)),
            # PrintScreen debug composites — OFF by default, for bug reports.
            "debug_shots": str(_ocr_p.get("debug_shots", 0)),
        }
        if "scan_bind_toggle" not in _ocr_p:
            # ONE-TIME legacy migration, keyed on the CONFIG FILE: a single
            # scan_bind fills the slot its old scan_mode selected. (Keying
            # this on the in-memory dict re-ran it every app launch and
            # scrambled saved binds — "it forgets my binds each version".)
            if self._ocr_style["scan_mode"] == "toggle":
                self._ocr_style["scan_bind_toggle"] = \
                    self._ocr_style["scan_bind"]
                self._ocr_style["scan_bind"] = ""
        self.on_ocr_style_change = None  # (prototype) callback(key, value)
        self.ocr_log_chat = bool(_ocr_p.get("log_chat", False))
        self.on_ocr_foreign_change = None  # (prototype) callback(bool)
        self.on_ocr_ignore_names_change = None  # (prototype) callback(bool)
        self.on_ocr_region_set = None  # (prototype) callback() — fresh drag
        self.on_language_change = None
        self.on_recent_languages_change = None
        self.on_nav_change: Callable[[int], None] | None = None
        self.on_filter_peer_by_target_languages_change = None
        self.runtime_log_detailed: Callable[..., bool | None] | None = None

        self._build_ui()

    # ── Build ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Toggle rows ──────────────────────────────────────────────────────
        self._row_stt = _ToggleRow(ft.Icons.MIC, t("dashboard.stt_label"), on_click=self._on_stt_click)
        self._row_stt.tooltip = "Click to toggle • Right-click to change STT provider"
        self._row_peer = _ToggleRow(ft.Icons.RECORD_VOICE_OVER, t("dashboard.peer_label"), on_click=self._on_peer_click)
        self._row_peer.tooltip = "Click to toggle peer translation • Right-click to change provider"
        self._row_trans = _ToggleRow(ft.Icons.TRANSLATE, t("dashboard.trans_label"), on_click=self._on_trans_click)
        self._row_overlay = _ToggleRow(ft.Icons.SUBTITLES, t("dashboard.overlay_label"), on_click=self._on_overlay_click)
        self._overlay_header_btn: ft.Container | None = None  # built later in chat header
        # (control, i18n_key) pairs for tooltips set once at construction. apply_locale
        # re-applies them so a runtime UI-language change updates the hover text too
        # (Flet tooltips don't re-evaluate t() on their own).
        self._static_tooltip_registry: list[tuple[Any, str]] = []

        self._sync_stt_button_state()
        self._sync_translation_button_state()
        self._sync_overlay_peer_buttons()

        # ── Language settings panel ──────────────────────────────────────────
        def _make_tab_btn(label: str, idx: int) -> ft.Container:
            is_active = (idx == self._active_preset)
            txt = ft.Text(
                label,
                size=12,
                color="#ffffff" if is_active else _TEXT_FAINT,
                weight=ft.FontWeight.W_700 if is_active else ft.FontWeight.NORMAL,
                text_align=ft.TextAlign.CENTER,
            )
            return ft.Container(
                content=txt,
                expand=True,
                height=28,
                bgcolor=_TOGGLE_ON if is_active else "#333537",
                border_radius=6,
                alignment=ft.alignment.center,
                on_click=lambda _, i=idx: self._on_preset_tab_click(i),
                on_hover=lambda e, t=txt: (
                    setattr(t, "color", "#ffffff" if e.data == "true" else (
                        "#ffffff" if t.weight == ft.FontWeight.W_700 else _TEXT_FAINT
                    ))
                    or (t.update() if t.page else None)
                ),
            )

        self._preset_tab_containers: list[ft.Container] = [
            _make_tab_btn("1", 0),
            _make_tab_btn("2", 1),
            _make_tab_btn("3", 2),
        ]

        def _make_lang_card(text: str, on_click) -> ft.Container:
            lbl = ft.Text(text, size=12, color=_TEXT_MUTED, no_wrap=True,
                         overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER,
                         expand=True)
            arrow = ft.Icon(ft.Icons.CHEVRON_RIGHT, size=12, color=_TEXT_FAINT)
            return ft.Container(
                content=ft.Row(
                    [lbl, arrow],
                    spacing=2,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor="#2a2b2e",
                border_radius=6,
                border=ft.border.all(1, "#3a3b3e"),
                padding=ft.padding.symmetric(horizontal=8, vertical=5),
                on_click=on_click,
                on_hover=lambda e, l=lbl: (
                    setattr(l, "color", _TOGGLE_ON if e.data == "true" else _TEXT_MUTED)
                    or (l.update() if l.page else None)
                ),
            )

        self._src_lang_card = _make_lang_card(
            language_name(self._source_lang_code), self._open_source_dialog
        )
        self._alt_src_lang_card = _make_lang_card(
            language_name(self._alt_source_lang_code or "ko"), self._open_alt_source_dialog
        )
        self._tgt1_lang_card = _make_lang_card(
            language_name(self._target_lang_code), self._open_target_dialog
        )
        # Inline transliteration chip rows (Show / Send Pinyin|Romaji)
        self._tgt1_translit_col = self._build_translit_col(self._target_lang_code)
        # Generic romanization toggle ("Show/Send Latin") shown only when your voice
        # or the peer voice is Auto Detect — so romanization can still be enabled when
        # the language isn't fixed (e.g. auto-detected Korean → romaja in the logs).
        self._auto_translit_col = self._build_translit_col("")
        self._auto_translit_col.visible = self._auto_translit_should_show()

        # + button next to tgt1 card — always visible, adds another target language
        self._plus_btn = ft.Container(
            content=ft.Icon(ft.Icons.ADD_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
            on_click=self._on_add_extra_target,
            tooltip=t("dashboard.tooltip.add_target"),
            padding=ft.padding.only(left=4),
        )

        # Fixed width for button slot so all card rows align identically
        _BTN_SLOT = 22

        # Dynamic column for extra target rows (rebuilt when targets change)
        self._extra_tgt_rows_col = ft.Column(
            [],
            spacing=3,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self._swap_row_text = None  # no longer used
        swap_row = ft.Row(
            [
                ft.Container(
                    content=ft.Icon(ft.Icons.SWAP_VERT, size=14, color=_TEXT_FAINT),
                    expand=True,
                    alignment=ft.alignment.center,
                    on_click=self._swap_languages,
                    on_hover=self._on_swap_hover,
                    padding=ft.padding.symmetric(vertical=2),
                ),
                ft.Container(width=_BTN_SLOT),
            ],
            spacing=4,
        )

        _your_lang_info = ft.Container(
            content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11, color="#5a5b60"),
            tooltip=t("dashboard.tooltip.your_language_info"),
            padding=ft.padding.only(left=4),
        )
        self._static_tooltip_registry.append((_your_lang_info, "dashboard.tooltip.your_language_info"))
        self._lbl_your_language = ft.Text(t("dashboard.your_language"), size=10, color="#c8c9cc")
        _your_lang_label_row = ft.Row(
            [
                self._lbl_your_language,
                _your_lang_info,
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        )

        # All card rows share the same structure: [card (expand), fixed-width slot]
        # so every card is the same width regardless of which slot has a button.
        self._src_lang_card.expand = True
        self._tgt1_lang_card.expand = True

        _src_row = ft.Row(
            [self._src_lang_card, ft.Container(width=_BTN_SLOT)],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        _tgt_plus_slot = ft.Container(content=self._plus_btn, width=_BTN_SLOT, alignment=ft.alignment.center_left)
        _tgt1_with_plus = ft.Row(
            [self._tgt1_lang_card, _tgt_plus_slot],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Alt-source (bilingual quick-switch) controls — must be defined before lang_panel
        self._src_plus_btn = ft.Container(
            content=ft.Icon(ft.Icons.ADD_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
            on_click=self._on_add_alt_source,
            tooltip=t("dashboard.tooltip.add_second_spoken"),
            visible=self._alt_source_lang_code is None,
            padding=ft.padding.only(left=4),
        )
        self._src_minus_btn = ft.Container(
            content=ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
            on_click=self._on_remove_alt_source,
            tooltip=t("dashboard.tooltip.remove_second_spoken"),
            padding=ft.padding.only(left=4),
        )
        self._src_lang_card.expand = True
        self._alt_src_lang_card.expand = True
        _src_plus_slot = ft.Container(
            content=self._src_plus_btn,
            width=_BTN_SLOT,
            alignment=ft.alignment.center_left,
        )
        _src_with_plus = ft.Row(
            [self._src_lang_card, _src_plus_slot],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._alt_src_row = ft.Column(
            [
                ft.Row(
                    [self._alt_src_lang_card, self._src_minus_btn],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=3,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            visible=self._alt_source_lang_code is not None,
        )

        self._preset_tabs_row = ft.Row(
            self._preset_tab_containers,
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        self._lbl_translate_to = ft.Text(t("dashboard.translate_to"), size=10, color="#c8c9cc")
        _translate_to_info = ft.Container(
            content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11, color=_TEXT_FAINT),
            tooltip=t("dashboard.tooltip.translate_to"),
            padding=ft.padding.only(left=2),
        )
        self._static_tooltip_registry.append(
            (_translate_to_info, "dashboard.tooltip.translate_to"))
        _translate_to_label = ft.Row(
            [self._lbl_translate_to, _translate_to_info],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        lang_panel = ft.Container(
            content=ft.Column(
                [
                    _translate_to_label,
                    _tgt1_with_plus,
                    self._tgt1_translit_col,
                    self._auto_translit_col,
                    self._extra_tgt_rows_col,
                ],
                spacing=3,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            padding=ft.padding.only(top=8),
        )

        # ── Peer language panel (vertical layout, matches main section) ─────────
        self._peer_src_card = _make_lang_card(
            language_name(self._effective_peer_source_lang_code()),
            self._open_peer_source_dialog,
        )
        self._peer_tgt_card = _make_lang_card(
            language_name(self._effective_peer_target_lang_code()),
            self._open_peer_target_dialog,
        )
        self._peer_plus_btn = ft.Container(
            content=ft.Icon(ft.Icons.ADD_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
            on_click=self._on_add_extra_peer_target,
            tooltip=t("dashboard.tooltip.add_peer_target"),
            padding=ft.padding.only(left=4),
        )
        self._peer_src_card.expand = True
        self._peer_tgt_card.expand = True
        self._peer_src_plus_btn = ft.Container(
            content=ft.Icon(ft.Icons.ADD_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
            on_click=self._on_add_extra_peer_source,
            tooltip=t("dashboard.tooltip.listen_another_peer"),
            padding=ft.padding.only(left=4),
        )
        self._peer_src_plus_slot = ft.Container(
            content=self._peer_src_plus_btn,
            width=_BTN_SLOT,
            alignment=ft.alignment.center_left,
        )
        _peer_src_row = ft.Row(
            [self._peer_src_card, self._peer_src_plus_slot],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._extra_peer_src_rows_col = ft.Column(
            [],
            spacing=3,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        _peer_tgt_plus_slot = ft.Container(content=self._peer_plus_btn, width=_BTN_SLOT, alignment=ft.alignment.center_left)
        _peer_tgt_with_plus = ft.Row(
            [self._peer_tgt_card, _peer_tgt_plus_slot],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._extra_peer_tgt_rows_col = ft.Column(
            [],
            spacing=3,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self._lbl_you_speak = ft.Text(t("dashboard.you_speak"), size=10, color="#c8c9cc")
        _you_speak_info = ft.Container(
            content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11, color=_TEXT_FAINT),
            tooltip=t("dashboard.tooltip.your_spoken_lang"),
            padding=ft.padding.only(left=2),
        )
        self._static_tooltip_registry.append((_you_speak_info, "dashboard.tooltip.your_spoken_lang"))
        _peer_speaks_info = ft.Container(
            content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11, color=_TEXT_FAINT),
            tooltip=t("dashboard.tooltip.peer_spoken_lang"),
            padding=ft.padding.only(left=2),
        )
        self._static_tooltip_registry.append((_peer_speaks_info, "dashboard.tooltip.peer_spoken_lang"))
        self._lbl_peer_voice = ft.Text(t("dashboard.language.peer"), size=10, color="#c8c9cc")
        self._peer_panel = ft.Container(
            content=ft.Column(
                [
                    # "Your language" first, then "Target language" below.
                    ft.Row(
                        [self._lbl_you_speak, _you_speak_info],
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    _src_with_plus,
                    self._alt_src_row,
                    ft.Divider(height=5, color=_DIVIDER, thickness=1),
                    ft.Row(
                        [self._lbl_peer_voice, _peer_speaks_info],
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    _peer_src_row,
                    self._extra_peer_src_rows_col,
                ],
                spacing=3,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                tight=True,
            ),
            padding=ft.padding.only(top=5),
        )
        # Compat shims — not shown in UI but kept so _refresh_language_rows doesn't crash
        self._self_lang_row = None
        self._peer_lang_row = None
        self._lang_panel = lang_panel

        # ── Sidebar nav — only Settings gear (others in top bar when active) ──
        self._sidebar_nav_icons: list[ft.Icon] = [
            ft.Icon(ft.Icons.GRID_VIEW, size=20, color=_TOGGLE_ON),   # idx 0 dashboard
            ft.Icon(ft.Icons.SETTINGS, size=20, color=_TEXT_FAINT),   # idx 1 settings
            ft.Icon(ft.Icons.ARTICLE, size=20, color=_TEXT_FAINT),    # idx 2 logs
            ft.Icon(ft.Icons.INFO_OUTLINE, size=20, color=_TEXT_FAINT), # idx 3 about
        ]
        gear_icon = self._sidebar_nav_icons[1]
        gear_btn = ft.Container(
            content=gear_icon,
            width=44,
            height=40,
            alignment=ft.alignment.center,
            border_radius=6,
            bgcolor=ft.Colors.TRANSPARENT,
            on_click=lambda _: self._on_sidebar_nav_click(1),
            on_hover=lambda e: self._on_sidebar_nav_hover(e, 1),
        )
        # ── Self-update button (hidden unless an update exists) ──────────────
        self.on_update_click: object = None  # callback() — download / restart per flow state
        self._update_btn = _UpdateNavBtn(on_click=self._on_update_btn_click)
        self._mini_update_btn = _UpdateNavBtn(on_click=self._on_update_btn_click)
        # ── Translator selector button ────────────────────────────────────────
        self._translator_label_text = ft.Text(
            "Translator", size=10, color=_TEXT_FAINT, weight=ft.FontWeight.W_600,
            text_align=ft.TextAlign.CENTER,
        )
        self._translator_value_text = ft.Text(
            "—", size=11, color=_TEXT_MUTED, text_align=ft.TextAlign.LEFT,
            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
        )
        self._translator_btn = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.BOLT, size=13, color=_TEXT_FAINT),
                    self._translator_value_text,
                    ft.Icon(ft.Icons.EXPAND_MORE, size=13, color=_TEXT_FAINT),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                tight=False,
            ),
            padding=ft.padding.symmetric(horizontal=8, vertical=7),
            border_radius=6,
            bgcolor="#252628",
            border=ft.border.all(1, "#4a4b4f"),
            on_click=self._on_translator_btn_click,
            on_hover=lambda e: (
                setattr(e.control, "bgcolor", "#2e3032" if e.data == "true" else "#252628")
                or setattr(e.control, "border", ft.border.all(1, _TOGGLE_ON if e.data == "true" else "#4a4b4f"))
                or (e.control.update() if e.control.page else None)
            ),
            tooltip=t("dashboard.tooltip.change_model"),
            expand=True,
        )
        self.on_translator_change: object = None  # callback(model_value: str)
        self.on_request_current_translator: object = None  # callback() → live model value
        # Whisper availability (its model downloads from HuggingFace, which can be blocked).
        # When unavailable, the STT picker greys Whisper out with this reason instead of
        # letting the user pick a model that can't load.
        self._whisper_available: bool = True
        self._whisper_unavailable_reason: str = ""
        self.on_request_deepl_usage_refresh: object = None  # callback() → refresh translator usage
        self._current_translator_label: str = ""
        self._translator_usage_text: str | None = None  # API-usage line for TRANS tooltip
        self.on_stt_provider_change: object = None  # callback(provider_value: str)
        self.on_peer_stt_provider_change: object = None  # callback(provider_value: str)
        self._stt_provider_has_key: dict[str, bool] = {}  # provider_value → has key
        self._translator_model_has_key: dict[str, bool] = {}  # model_value → has key
        self.on_transliteration_change: object = None  # callback(show_pinyin, send_pinyin, show_romaji, send_romaji)
        self.on_overlay_lock_change: object = None  # callback(locked: bool)
        self.on_chatbox_send_peer_toggle: object = None  # callback(value: bool)
        self.on_loopback_mode_change: object = None  # callback(selected_only: bool)
        self.on_loopback_translation_only_change: object = None  # callback(translation_only: bool)
        self.on_self_in_overlay_toggle: object = None  # callback(value: bool) — spoken
        self.on_typed_in_overlay_toggle: object = None  # callback(value: bool) — typed
        self.on_vrc_mute_sync_toggle: object = None  # callback(value: bool)
        self.on_overlay_transparency_change: object = None  # callback(alpha: float)
        self.on_overlay_mode_select: object = None  # callback(mode: "auto"|"steamvr"|"desktop")
        self.on_overlay_single_turn_change: object = None  # callback(value: bool)
        self.on_overlay_display_toggle: object = None  # callback(field: str, value: bool)
        self.on_overlay_size_select: object = None  # callback(size_preset: str)
        self._overlay_size_preset: str = "small"  # current desktop size preset; synced from settings
        self._overlay_show_original: bool = True
        self._overlay_show_translation: bool = True
        self._overlay_show_romanization: bool = True
        self._overlay_locked: bool = False
        self._overlay_background_alpha: float = 0.5
        self._overlay_target_pref: str = "steamvr"  # stored preference (not active)
        self._overlay_auto_switch: bool = True
        self._overlay_single_turn: bool = True
        self._overlay_active: bool = False  # overlay currently on
        self._overlay_mode_value: str | None = None  # resolved target (steamvr/desktop)

        self._sidebar_nav_row = ft.Container(
            content=ft.Row(
                # Invisible counterweight mirrors the update button's reserved
                # slot so the gear sits dead-center whether or not an update is
                # showing; the update button fades in to the gear's right.
                [ft.Container(width=44, height=40), gear_btn, self._update_btn],
                spacing=6,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=8, vertical=6),
        )

        # ── Sidebar collapse support ─────────────────────────────────────────
        self._mini_stt_btn = _MiniIconBtn(ft.Icons.MIC, t("dashboard.stt_label"), on_click=self._on_stt_click)
        self._mini_peer_btn = _MiniIconBtn(ft.Icons.RECORD_VOICE_OVER, t("dashboard.peer_label"), on_click=self._on_peer_click)
        self._mini_trans_btn = _MiniIconBtn(ft.Icons.TRANSLATE, t("dashboard.trans_label"), on_click=self._on_trans_click)
        self._mini_gear_btn = _MiniIconBtn(ft.Icons.SETTINGS, "Settings", on_click=lambda _: self._on_sidebar_nav_click(1))
        self._mini_lang_text = ft.Text(
            "—", size=9, color=_TEXT_FAINT, text_align=ft.TextAlign.CENTER,
            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=t("dashboard.tooltip.language_settings_expand"),
        )
        _mini_lang_tap = ft.GestureDetector(
            content=ft.Container(
                content=self._mini_lang_text,
                width=44, alignment=ft.alignment.center,
                padding=ft.padding.symmetric(vertical=3),
                border_radius=4,
                on_hover=lambda e: (
                    setattr(e.control, "bgcolor", "#3f4044" if e.data == "true" else ft.Colors.TRANSPARENT)
                    or (e.control.update() if e.control.page else None)
                ),
            ),
            on_tap=self._on_sidebar_collapse_click,
        )
        self._mini_content = ft.Column(
            [
                ft.Container(height=4),
                self._mini_stt_btn,
                self._mini_peer_btn,
                self._mini_trans_btn,
                ft.Divider(height=1, color=_DIVIDER, thickness=1),
                _mini_lang_tap,
                ft.Divider(height=1, color=_DIVIDER, thickness=1),
                self._mini_gear_btn,
                self._mini_update_btn,
            ],
            spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
        )
        # Sync initial mini states
        self._mini_stt_btn.set_state(self.is_stt_on)
        self._mini_trans_btn.set_state(self.is_translation_on)

        # ── Sidebar header ───────────────────────────────────────────────────
        self._collapse_icon = ft.Icon(ft.Icons.CHEVRON_LEFT, size=16, color=_TEXT_FAINT)
        self._collapse_btn_ctrl = ft.Container(
            content=self._collapse_icon,
            on_click=self._on_sidebar_collapse_click,
            tooltip=t("dashboard.tooltip.collapse_sidebar"),
            padding=ft.padding.all(4),
            border_radius=4,
            on_hover=lambda e: (
                setattr(e.control, "bgcolor", "#3f4044" if e.data == "true" else ft.Colors.TRANSPARENT)
                or (e.control.update() if e.control.page else None)
            ),
        )
        self._sidebar_puri_text = ft.Text("PuriPulyHeart+", size=14, weight=ft.FontWeight.BOLD, color=_TOGGLE_ON)
        self._sidebar_tag_text = ft.Text(_BUILD_TAG, size=10, color=_TEXT_FAINT)
        self._sidebar_header_spacer = ft.Container(expand=True)
        self._sidebar_header_row = ft.Row(
            [
                self._sidebar_puri_text,
                self._sidebar_tag_text,
                self._sidebar_header_spacer,
                self._collapse_btn_ctrl,
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._sidebar_header = ft.Container(
            content=self._sidebar_header_row,
            padding=ft.padding.symmetric(horizontal=16, vertical=10),
        )

        # ── Sidebar ──────────────────────────────────────────────────────────
        # Middle section is scrollable so the gear icon always stays visible
        # even when Target Language 2 is added.
        _CARD_BG = "#2a2b2e"
        _CARD_BORDER = "#454648"
        _CARD_ICON_COLOR = "#c8c9cc"

        self._section_header_labels: list[tuple[ft.Text, str]] = []

        def _section_card(icon: str, label_key: str, content: ft.Control,
                          trailing: ft.Control | None = None) -> ft.Container:
            _lbl = ft.Text(t(label_key), size=11, color=_TOGGLE_ON, weight=ft.FontWeight.W_700)
            self._section_header_labels.append((_lbl, label_key))
            header_controls: list[ft.Control] = [ft.Icon(icon, size=15, color=_CARD_ICON_COLOR), _lbl]
            if trailing is not None:
                header_controls.append(ft.Container(expand=True))
                header_controls.append(trailing)
            return ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            header_controls,
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        content,
                    ],
                    spacing=0,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                bgcolor=_CARD_BG,
                border=ft.border.all(1, _CARD_BORDER),
                border_radius=10,
                padding=ft.padding.all(8),
                margin=ft.margin.symmetric(horizontal=6, vertical=3),
            )

        _preset_row = ft.Container(
            content=self._preset_tabs_row,
            padding=ft.padding.symmetric(horizontal=10, vertical=4),
        )
        # Both cards are ALWAYS built so the unified/separate layouts can swap
        # live (settings toggle) without rebuilding the sidebar. Unified view:
        # the voice card is titled "Translation", the Text Translation card is
        # hidden, and typed messages mirror the Voice "Translate from"
        # (partner's) language. When "Translate from" is Auto Detect there is
        # no concrete language to mirror, so the Text Translation card is
        # revealed for the user to pick the typed-output language. The options
        # gear lives on the voice card in both layouts (one control instance
        # can't be parented twice).
        _voice_key = ("dashboard.section.translation" if self._unified_translation
                      else "dashboard.section.voice_translation")
        self._text_section_card = _section_card(
            ft.Icons.CHAT_BUBBLE_OUTLINE, "dashboard.section.text_translation",
            self._lang_panel)
        self._voice_section_card = _section_card(
            ft.Icons.GRAPHIC_EQ, _voice_key,
            self._peer_panel,
            trailing=ft.Row(
                [self._build_lang_swap_btn(), self._build_translit_gear()],
                spacing=2, tight=True,
            ))
        self._voice_section_lbl = self._section_header_labels[-1][0]
        self._preset_row_container = _preset_row
        self._apply_unified_target_sync()  # also sets text-card visibility
        if self._unified_translation:
            _cards = [self._voice_section_card, self._text_section_card]
        else:
            _cards = [self._text_section_card, self._voice_section_card]
        self._middle_section = ft.Column(
            [_preset_row, *_cards],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=0,
        )
        self._toggles_section = ft.Container(
            content=ft.Column(
                [
                    ft.GestureDetector(content=self._row_stt, on_secondary_tap=self._on_stt_right_click),
                    ft.GestureDetector(content=self._row_peer, on_secondary_tap=self._on_peer_right_click),
                    ft.GestureDetector(content=self._row_trans, on_secondary_tap=self._on_trans_right_click),
                ],
                spacing=4,
            ),
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
        )
        self._full_div1 = ft.Divider(height=1, color=_DIVIDER, thickness=1)
        self._full_div2 = ft.Divider(height=1, color=_DIVIDER, thickness=1)
        sidebar = ft.Container(
            content=ft.Column(
                [
                    self._sidebar_header,
                    ft.Divider(height=1, color=_DIVIDER, thickness=1),
                    self._toggles_section,
                    self._full_div1,
                    self._middle_section,
                    self._full_div2,
                    self._sidebar_nav_row,
                    self._mini_content,
                ],
                spacing=0,
                expand=True,
            ),
            bgcolor=_BG_SIDEBAR,
            width=220,
            expand=False,
        )
        self._sidebar_container = sidebar

        # ── Hidden display card (controller API compat — not shown in UI) ────
        self.display_card = DisplayCard(
            on_submit=self._on_submit,
            on_input_focus_change=self._set_message_input_focused,
        )
        self.display_card.visible = False

        # ── Status notice strip (shown when there's a notice) ────────────────
        self.on_request_stt_download: object = None  # callback() → triggers model download
        self._notice_text_ctrl = ft.Text("", size=12, color=_TOGGLE_WARNING, expand=True)
        self._notice_download_btn = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.DOWNLOAD, size=12, color="#ffffff"),
                    ft.Text(t("dashboard.download"), size=11, color="#ffffff", weight=ft.FontWeight.W_600),
                ],
                spacing=3,
                tight=True,
            ),
            bgcolor=_TOGGLE_ON,
            border_radius=4,
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            on_click=lambda _: self.on_request_stt_download() if callable(self.on_request_stt_download) else None,
            visible=False,
            tooltip=t("dashboard.tooltip.download_qwen"),
        )
        self._notice_strip = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=_TOGGLE_WARNING),
                    self._notice_text_ctrl,
                    self._notice_download_btn,
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            # Attached bar across the top of the chat box: solid background,
            # square corners, toned bottom border — part of the frame, not a
            # floating chip.
            bgcolor="#2b3032",
            border=ft.border.only(bottom=ft.BorderSide(1, ft.Colors.with_opacity(0.55, _TOGGLE_WARNING))),
            padding=ft.padding.symmetric(horizontal=10, vertical=6),
            visible=False,
        )

        # ── Chat log ─────────────────────────────────────────────────────────
        # A Column (NOT a virtualized ListView) so the wrapping SelectionArea can
        # drag-select text across multiple messages — ListView only realizes the
        # visible rows, which breaks cross-message selection. Capped by
        # CHAT_MAX_ENTRIES so the un-virtualized list stays small.
        # _chat_following: when True, new messages auto-scroll to the bottom. Set False
        # the moment the user scrolls up (see _on_chat_scroll) so a new message never
        # yanks them away from what they're reading; the floating jump button resumes it.
        self._chat_following = True
        self._chat_list_view = ft.Column(
            controls=self._chat_entries,
            expand=True,
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
            # auto_scroll=False: following is controlled manually based on scroll position
            # (auto_scroll=True would force every new message to the bottom, interrupting
            # the user mid-read).
            auto_scroll=False,
            on_scroll=self._on_chat_scroll,
            # Stretch entries to full width (a plain Column otherwise shrinks each row to
            # its content — the narrow-box "bad theme" regression vs the old ListView).
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self._chat_clear_button = ft.TextButton(
            t("dashboard.clear") if t("dashboard.clear") != "dashboard.clear" else "Clear",
            style=ft.ButtonStyle(
                color={ft.ControlState.DEFAULT: _TEXT_FAINT, ft.ControlState.HOVERED: _TEXT_MUTED},
                overlay_color=ft.Colors.TRANSPARENT,
                padding=ft.padding.all(0),
            ),
            on_click=self._on_chat_clear,
        )
        _pill_border_off = ft.border.all(1, "#3a3b3f")
        _pill_border_on = ft.border.all(1, _TOGGLE_ON)
        _pill_border_peer = ft.border.all(1, _RECV_COLOR)
        # Filter button — default ON (matches _filter_peer_lang_active = True)
        self._filter_peer_btn = ft.Container(
            content=ft.Text(
                t("dashboard.button.target_langs_only"),
                size=9,
                color=_RECV_COLOR,
                weight=ft.FontWeight.W_600,
            ),
            on_click=self._on_chat_filter_peer_click,
            tooltip=t("dashboard.tooltip.filter_peer"),
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            border_radius=10,
            bgcolor="#2d1f33",
            border=_pill_border_peer,
        )
        self._overlay_header_text = ft.Text(
            t("dashboard.button.overlay"), size=9, color=_TEXT_FAINT, weight=ft.FontWeight.W_600,
        )
        # Small VR/Desktop indicator so it's obvious where the overlay renders — a
        # frequent source of "why can't I see it?" when it's in VR mode on desktop.
        self._overlay_mode_text = ft.Text(
            "", size=8, color=_TEXT_FAINT, weight=ft.FontWeight.W_700,
        )
        self._overlay_mode_chip = ft.Container(
            content=self._overlay_mode_text,
            padding=ft.padding.symmetric(horizontal=4, vertical=1),
            border_radius=5,
            bgcolor="#33343a",
            visible=False,
            margin=ft.margin.only(left=4),
        )
        self._overlay_lock_icon = ft.Icon(ft.Icons.LOCK_OPEN, size=11, color=_TEXT_FAINT)
        _overlay_divider = ft.Container(
            width=1, height=12,
            bgcolor="#4a4b4f",
        )
        self._overlay_main_tip_box = ft.Container(
            content=ft.Row(
                [self._overlay_header_text, self._overlay_mode_chip],
                spacing=0,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=self._on_overlay_click,
            tooltip=t("dashboard.overlay.tooltip"),
            padding=ft.padding.only(left=8, right=6, top=3, bottom=3),
        )
        self._static_tooltip_registry.append((self._overlay_main_tip_box, "dashboard.overlay.tooltip"))
        _overlay_left = ft.GestureDetector(
            content=self._overlay_main_tip_box,
            on_secondary_tap_down=self._on_overlay_right_click,
        )
        self._overlay_lock_side = ft.Container(
            content=self._overlay_lock_icon,
            on_click=self._on_overlay_lock_click,
            tooltip=t("dashboard.overlay.lock.unlocked"),
            padding=ft.padding.only(left=5, right=7, top=3, bottom=3),
        )
        self._overlay_header_btn = ft.Container(
            content=ft.Row(
                [_overlay_left, _overlay_divider, self._overlay_lock_side],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            border_radius=10,
            bgcolor=ft.Colors.TRANSPARENT,
            border=_pill_border_off,
            padding=0,
        )
        self._chatbox_peer_btn = ft.Container(
            content=ft.Text(
                t("dashboard.button.loopback"),
                size=9,
                color=_TEXT_FAINT,
                weight=ft.FontWeight.W_600,
            ),
            on_click=self._on_chatbox_peer_btn_click,
            tooltip=t("dashboard.loopback.tooltip"),
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            border_radius=10,
            bgcolor=ft.Colors.TRANSPARENT,
            border=_pill_border_off,
        )
        self._static_tooltip_registry.append((self._chatbox_peer_btn, "dashboard.loopback.tooltip"))
        # ── OCR detection toggle (prototype) — red boxes around on-screen text ──
        self._ocr_btn = ft.Container(
            content=ft.Text("OCR", size=9, color=_TEXT_FAINT, weight=ft.FontWeight.W_600),
            on_click=self._on_ocr_btn_click,
            tooltip="Detect on-screen text and outline it (prototype).\n"
                    "Alt+T: swap in recognized text. Right-click: options.",
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            border_radius=10,
            bgcolor=ft.Colors.TRANSPARENT,
            border=_pill_border_off,
        )
        self._vrc_mute_sync_btn = ft.Container(
            content=ft.Text(
                t("dashboard.button.mute_sync"),
                size=9,
                color=_TEXT_FAINT,
                weight=ft.FontWeight.W_600,
            ),
            on_click=self._on_vrc_mute_sync_click,
            tooltip=t("dashboard.mute_sync.tooltip.off"),
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            border_radius=10,
            bgcolor=ft.Colors.TRANSPARENT,
            border=_pill_border_off,
        )
        self._chat_header_label = ft.Text(t("dashboard.chat"), size=11, color=_TEXT_FAINT, weight=ft.FontWeight.W_500)
        chat_header = ft.Row(
            [
                self._chat_header_label,
                ft.Container(expand=True),
                self._vrc_mute_sync_btn,
                ft.Container(width=4),
                ft.GestureDetector(
                    content=self._chatbox_peer_btn,
                    on_secondary_tap_down=self._on_loopback_right_click,
                ),
                ft.Container(width=4),
                ft.GestureDetector(
                    content=self._ocr_btn,
                    on_secondary_tap_down=self._on_ocr_right_click,
                ),
                ft.Container(width=4),
                self._overlay_header_btn,
                ft.Container(width=4),
                self._chat_clear_button,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
        )
        # Floating "jump to latest" button (Discord-style), shown only when the user has
        # scrolled up. Clicking it returns to the newest message and resumes following.
        self._chat_jump_btn = ft.Container(
            content=ft.Icon(ft.Icons.KEYBOARD_DOUBLE_ARROW_DOWN_ROUNDED, size=18, color="#e8e8e8"),
            width=32,
            height=32,
            border_radius=16,
            bgcolor="#3a3b3e",
            border=ft.border.all(1, "#55565a"),
            alignment=ft.alignment.center,
            on_click=self._on_jump_to_latest,
            tooltip=t("dashboard.jump_latest") if t("dashboard.jump_latest") != "dashboard.jump_latest" else "Jump to latest",
            visible=False,
        )
        # The message list (SelectionArea enables free Copy/Select-all of log text) plus
        # the floating jump button, stacked so the button overlays the bottom-center.
        chat_box = ft.Container(
            content=ft.Column(
                [
                    # Notice banner is attached to the top edge of the chat box
                    # (full width, square corners, bottom border) — inside the
                    # box so it never pushes the chat header or panel around;
                    # only the messages reflow beneath it.
                    self._notice_strip,
                    ft.Container(
                        content=ft.Stack(
                            [
                                ft.Container(
                                    left=0, top=0, right=0, bottom=0,
                                    # No custom right-click here: Flutter's native
                                    # selection menu (Select all) can't be suppressed
                                    # or extended from Flet, so an app menu on the
                                    # same click doubles up.
                                    content=ft.SelectionArea(content=self._chat_list_view),
                                ),
                                ft.Container(
                                    left=0, right=0, bottom=6,
                                    alignment=ft.alignment.center,
                                    content=self._chat_jump_btn,
                                ),
                            ],
                            expand=True,
                        ),
                        padding=ft.padding.all(8),
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            expand=True,
            bgcolor=_BG_CHAT,
            border_radius=6,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

        # ── Message input at bottom (VRCT style) ─────────────────────────────
        self._msg_input = ft.TextField(
            hint_text=t("display.input_hint"),
            border=ft.InputBorder.OUTLINE,
            border_color=_BORDER_INPUT,
            focused_border_color=_TOGGLE_ON,
            text_size=13,
            color=_TEXT_PRIMARY,
            hint_style=ft.TextStyle(color=_TEXT_FAINT, italic=True),
            expand=True,
            multiline=True,
            min_lines=2,
            max_lines=4,
            shift_enter=True,
            on_submit=self._on_msg_input_submit,
            on_focus=lambda _: self._set_message_input_focused(True),
            on_blur=lambda _: self._set_message_input_focused(False),
            bgcolor=_BG_INPUT,
            border_radius=8,
            content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
        )
        input_row = ft.Container(
            content=ft.Row(
                [
                    self._msg_input,
                    ft.IconButton(
                        ft.Icons.SEND_ROUNDED,
                        icon_size=18,
                        icon_color=_TOGGLE_ON,
                        on_click=self._on_send_btn_click,
                        style=ft.ButtonStyle(
                            overlay_color=ft.Colors.TRANSPARENT,
                            padding=ft.padding.all(8),
                        ),
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=8, vertical=6),
            bgcolor=_BG_MAIN,
        )

        # ── Right panel ──────────────────────────────────────────────────────
        right_panel = ft.Container(
            content=ft.Column(
                [
                    chat_header,
                    chat_box,
                    ft.Divider(height=1, color=_DIVIDER, thickness=1),
                    input_row,
                ],
                spacing=4,
                expand=True,
                # STRETCH so the chat box fills the FULL width even when empty. Without
                # it the column left-aligns children at their content width, so the empty
                # chat box collapsed to a narrow left strip (the "black bar") and only
                # widened once a message made its content wide — which also meant
                # right-click/selection only worked inside that strip.
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            expand=True,
            bgcolor=_BG_MAIN,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
        )

        self.controls = [sidebar, right_panel]

    # ── Sidebar nav ──────────────────────────────────────────────────────────

    def _on_sidebar_nav_click(self, idx: int) -> None:
        for i, ic in enumerate(self._sidebar_nav_icons):
            ic.color = _TOGGLE_ON if i == idx else _TEXT_FAINT
            try:
                ic.update()
            except Exception:
                pass
        if self.on_nav_change:
            self.on_nav_change(idx)

    def _on_sidebar_nav_hover(self, e, idx: int) -> None:
        container = e.control
        container.bgcolor = "#3f4044" if e.data == "true" else ft.Colors.TRANSPARENT
        try:
            container.update()
        except Exception:
            pass

    def _on_sidebar_collapse_click(self, e=None) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        collapsed = self._sidebar_collapsed

        # Arrow direction
        self._collapse_icon.name = ft.Icons.CHEVRON_RIGHT if collapsed else ft.Icons.CHEVRON_LEFT
        self._collapse_btn_ctrl.tooltip = "Expand sidebar" if collapsed else "Collapse sidebar"

        # Header: hide title text when collapsed, center the arrow
        self._sidebar_puri_text.visible = not collapsed
        self._sidebar_tag_text.visible = not collapsed
        self._sidebar_header_spacer.visible = not collapsed
        self._sidebar_header_row.alignment = (
            ft.MainAxisAlignment.CENTER if collapsed else ft.MainAxisAlignment.START
        )
        self._sidebar_header.padding = (
            ft.padding.symmetric(horizontal=4, vertical=14)
            if collapsed
            else ft.padding.symmetric(horizontal=16, vertical=14)
        )

        # Full content visibility
        self._toggles_section.visible = not collapsed
        self._full_div1.visible = not collapsed
        self._middle_section.visible = not collapsed
        self._full_div2.visible = not collapsed
        self._sidebar_nav_row.visible = not collapsed

        # Mini content visibility
        self._mini_content.visible = collapsed

        # Width
        self._sidebar_container.width = 56 if collapsed else 220

        try:
            if self.page:
                self.page.update()
        except Exception:
            pass

    def set_sidebar_nav_selected(self, idx: int) -> None:
        for i, ic in enumerate(self._sidebar_nav_icons):
            ic.color = _TOGGLE_ON if i == idx else _TEXT_FAINT
            try:
                ic.update()
            except Exception:
                pass

    # ── Self-update button ───────────────────────────────────────────────────

    def _on_update_btn_click(self, _e) -> None:
        if self.on_update_click:
            self.on_update_click()

    def set_update_flow_state(self, state: str, progress: float, visible: bool,
                              tooltip: str) -> None:
        self._update_btn.set_flow(state, progress, visible, tooltip)
        self._mini_update_btn.set_flow(state, progress, visible, tooltip)

    # ── Toggle click handlers ────────────────────────────────────────────────

    def _on_stt_click(self, e):
        self._toggle_stt()

    def _on_peer_click(self, e):
        self._row_peer.set_loading(True)
        self._toggle_peer_translation()

    def _on_trans_click(self, e):
        self._toggle_translation()

    def _on_overlay_click(self, e):
        self._row_overlay.set_loading(True)
        self._toggle_overlay()

    def _toggle_overlay(self) -> None:
        # Debounce: a double-fired click event (two toggles ~200ms apart) soft-hid
        # and re-revealed the overlay — seen live as a "random restart" with the
        # active banner popping up mid-session. Humans re-click slower than this.
        import time as _time
        now = _time.monotonic()
        last = getattr(self, "_last_overlay_toggle_ts", 0.0)
        if now - last < 0.4:
            logger.info("[Dashboard] Overlay toggle debounced (%.0fms since last)",
                        (now - last) * 1000)
            return
        self._last_overlay_toggle_ts = now
        enabled = True
        if self._overlay_peer_contract is not None:
            enabled = not self._overlay_peer_contract.overlay.intent_enabled
        if self.on_toggle_overlay:
            self.on_toggle_overlay(enabled)

    def set_ocr_on(self, on: bool) -> None:
        """Set the OCR pill state programmatically (module download flow:
        revert the optimistic flip when the module is missing, light it up
        after the download completes and OCR actually starts)."""
        self._ocr_on = bool(on)
        self._ocr_btn.border = ft.border.all(1, _TOGGLE_ON if on else "#3a3b3f")
        self._ocr_btn.content.color = _TOGGLE_ON if on else _TEXT_FAINT
        with contextlib.suppress(Exception):
            self._ocr_btn.update()

    def _on_ocr_btn_click(self, _e) -> None:
        # (Prototype) flip the OCR detection overlay on/off and reflect state in
        # the pill: teal border/text when active, faint when off.
        self._ocr_on = not self._ocr_on
        on = self._ocr_on
        self._ocr_btn.border = ft.border.all(1, _TOGGLE_ON if on else "#3a3b3f")
        self._ocr_btn.content.color = _TOGGLE_ON if on else _TEXT_FAINT
        with contextlib.suppress(Exception):
            self._ocr_btn.update()
        if callable(self.on_toggle_ocr):
            self.on_toggle_ocr(on)

    def _on_ocr_right_click(self, e) -> None:
        # Styled like the overlay right-click menu: label left, ON/OFF pill
        # right, toggles apply in place (menu stays open).
        x, y = self._tap_xy(e)
        region_on = False
        if callable(self.on_ocr_region_state):
            with contextlib.suppress(Exception):
                region_on = bool(self.on_ocr_region_state())

        def _section_row(label: str, control, top: int = 4,
                         tooltip: str | None = None) -> ft.Container:
            _row = [ft.Text(label, size=11, color=_TEXT_MUTED)]
            if tooltip:
                _row.append(ft.Container(
                    content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11,
                                    color=_TEXT_FAINT),
                    tooltip=tooltip,
                    padding=ft.padding.only(left=3),
                ))
            _row.append(ft.Container(expand=True))
            _row.append(control)
            return ft.Container(
                content=ft.Row(
                    _row, spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.padding.only(left=10, right=10, top=top, bottom=2),
            )


        def _row_tt(key: str, control, top: int = 4) -> ft.Container:
            # Labeled row with an auto info tooltip from '<key>.tooltip'.
            return _section_row(t(key), control, top=top,
                                tooltip=t(key + ".tooltip"))

        def _guarded(fn, val):
            try:
                if callable(fn):
                    fn(val)
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "[OCR] menu toggle failed")

        def _bool_pill(initial: bool, on_change) -> ft.Container:
            state = [bool(initial)]
            lbl = ft.Text(
                t("settings.option.on") if state[0]
                else t("settings.option.off"),
                size=11,
                color=_TOGGLE_ON if state[0] else _TEXT_FAINT,
                weight=ft.FontWeight.W_600,
            )
            box = ft.Container(
                content=lbl,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=6,
                border=ft.border.all(1, _TOGGLE_ON if state[0] else "#3a3b3f"),
            )

            def _click(_ev, _s=state, _l=lbl, _b=box):
                _s[0] = not _s[0]
                _l.value = (t("settings.option.on") if _s[0]
                            else t("settings.option.off"))
                _l.color = _TOGGLE_ON if _s[0] else _TEXT_FAINT
                _b.border = ft.border.all(
                    1, _TOGGLE_ON if _s[0] else "#3a3b3f")
                with contextlib.suppress(Exception):
                    _l.update()
                    _b.update()
                _guarded(on_change, _s[0])

            box.on_click = _click
            return box

        # ── target window: summary button + inline radio expansion ──
        def _win_summary() -> str:
            return (self._ocr_window_title
                    or t("dashboard.ocr.menu.window.whole"))

        _win_btn_text = ft.Text(
            _win_summary(), size=11, color=_TOGGLE_ON,
            weight=ft.FontWeight.W_600, no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        _win_btn = ft.Container(
            content=_win_btn_text,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6,
            bgcolor="#1a2e2a",
            border=ft.border.all(1, _TOGGLE_ON),
            width=150,
        )
        def _open_win_picker(_ev) -> None:
            # A dozen open windows would push the popover off screen —
            # use the scrollable SettingsModal picker (same style as the
            # Mic/PEER provider right-clicks). The OCR menu STAYS OPEN
            # underneath; the modal layers above it.
            if not self.page:
                return
            titles: list[str] = []
            try:
                if callable(self.on_ocr_window_list):
                    titles = list(self.on_ocr_window_list() or [])
            except Exception:
                titles = []
            cur = self._ocr_window_title
            options = [
                OptionItem(value="",
                           label=t("dashboard.ocr.menu.window.whole"),
                           description="", disabled=False),
                OptionItem(value="VRChat", label="VRChat",
                           description="", disabled=False),
            ]
            for w in titles:
                if w != "VRChat":
                    options.append(OptionItem(value=w, label=w,
                                              description="",
                                              disabled=False))
            if cur and all(o.value != cur for o in options):
                options.append(OptionItem(value=cur, label=cur,
                                          description="", disabled=False))

            def _sel(value: str) -> None:
                self._ocr_window_title = value
                _win_btn_text.value = _win_summary()
                with contextlib.suppress(Exception):
                    _win_btn_text.update()
                _guarded(self.on_ocr_window_change, value)

            SettingsModal(self.page, t("dashboard.ocr.menu.window"),
                          options, _sel).open(cur)

        _win_btn.on_click = _open_win_picker

        def _on_bubbles(v: bool) -> None:
            self._ocr_bubbles_only = v
            if callable(self.on_ocr_bubbles_change):
                self.on_ocr_bubbles_change(v)

        def _on_foreign(v: bool) -> None:
            self._ocr_foreign_only = v
            if callable(self.on_ocr_foreign_change):
                self.on_ocr_foreign_change(v)

        def _on_names(v: bool) -> None:
            self._ocr_ignore_names = v
            if callable(self.on_ocr_ignore_names_change):
                self.on_ocr_ignore_names_change(v)

        def _on_pronouns(v: bool) -> None:
            self._ocr_ignore_pronouns = v
            if callable(self.on_ocr_ignore_pronouns_change):
                self.on_ocr_ignore_pronouns_change(v)

        def _on_translate(v: bool) -> None:
            self._ocr_translate = v
            if callable(self.on_ocr_translate_change):
                self.on_ocr_translate_change(v)

        def _on_prewarm(v: bool) -> None:
            self._ocr_prewarm = v
            if callable(self.on_ocr_prewarm_change):
                self.on_ocr_prewarm_change(v)

        def _on_log(v: bool) -> None:
            self.ocr_log_chat = v
            self._save_ocr_pref("log_chat", v)

        def _on_lock(_v: bool) -> None:
            if callable(self.on_ocr_region_toggle):
                self.on_ocr_region_toggle()

        def _on_set_region(_ev) -> None:
            _close = getattr(self, "_ocr_popover_close", None)
            if callable(_close):
                _close()
            try:
                self._set_ocr_region()
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "[OCR] region select failed")

        # ── OCR translation model — the FULL translator list (same as the
        # TRANS menu). Free web engines run inside the overlay; paid/LLM
        # models route through the app's provider stack (user's own keys,
        # spent only when the user picks one here).
        from puripuly_heart.config.settings import TranslationModel as _TM
        _svc_names = {
            "bing": "Bing", "google": "Google", "papago": "Papago",
            _TM.GEMMA4.value: "Gemma 4 26B",
            _TM.DEEPSEEK_V4_FLASH.value: "DeepSeek V4 Flash",
            _TM.DEEPSEEK_V4_PRO.value: "DeepSeek V4 Pro",
            _TM.GEMINI_3_FLASH.value: "Gemini 3 Flash",
            _TM.GEMINI_31_FLASH_LITE.value: "Gemini 3.1 Flash-Lite",
            _TM.QWEN_35_PLUS.value: "Qwen 3.5 Plus",
            _TM.DEEPL.value: "DeepL",
            _TM.GOOGLE_TRANSLATE.value: "Google Translate",
            _TM.LOCAL_LLM.value: "Local LLMs",
        }
        _svc_needs_key = {
            _TM.DEEPSEEK_V4_PRO.value, _TM.GEMINI_3_FLASH.value,
            _TM.GEMINI_31_FLASH_LITE.value, _TM.QWEN_35_PLUS.value,
            _TM.DEEPL.value,
        }
        _svc_btn_text = ft.Text(
            _svc_names.get(self._ocr_xlat_service, "Bing"),
            size=11, color=_TOGGLE_ON, weight=ft.FontWeight.W_600,
            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
        )
        _svc_btn = ft.Container(
            content=_svc_btn_text,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6,
            bgcolor="#1a2e2a",
            border=ft.border.all(1, _TOGGLE_ON),
        )

        def _open_svc_picker(_ev) -> None:
            if not self.page:
                return
            _has_key = getattr(self, "_translator_model_has_key", {})
            _free = {"bing", "google", "papago"}
            # Gated on a VERIFIED key like the main translator picker — key
            # entry lives in the cog Settings, not here.
            options = []
            for k, v in _svc_names.items():
                needs = (k in _svc_needs_key
                         and not _has_key.get(k, False))
                desc = ""
                if k in _free:
                    desc = "(free)"
                elif needs:
                    desc = t("settings_modal.requires_api_key")
                options.append(OptionItem(value=k, label=v,
                                          description=desc,
                                          disabled=needs))
            options.sort(key=lambda o: o.disabled)

            def _sel(value: str) -> None:
                self._ocr_xlat_service = value
                _svc_btn_text.value = _svc_names.get(value, "Bing")
                with contextlib.suppress(Exception):
                    _svc_btn_text.update()
                self._save_ocr_pref("xlat_service", value)
                if callable(self.on_ocr_xlat_service_change):
                    _guarded(self.on_ocr_xlat_service_change, value)

            SettingsModal(self.page, t("dashboard.ocr.menu.xlat_model"),
                          options, _sel,
                          show_description=True).open(
                self._ocr_xlat_service)

        _svc_btn.on_click = _open_svc_picker

        set_btn = ft.Container(
            content=ft.Icon(ft.Icons.HIGHLIGHT_ALT, size=15,
                            color=_TEXT_PRIMARY),
            padding=ft.padding.symmetric(horizontal=8, vertical=3),
            border_radius=6,
            border=ft.border.all(1, "#3a3b3f"),
            on_click=_on_set_region,
        )
        lock_pill = _bool_pill(region_on, _on_lock)

        def _summary_btn(text_ctrl) -> ft.Container:
            return ft.Container(
                content=text_ctrl,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=6,
                bgcolor="#1a2e2a",
                border=ft.border.all(1, _TOGGLE_ON),
            )

        def _modal_row_btn(current_label: str, title: str,
                           options: list, pref_key: str,
                           label_of) -> ft.Container:
            btxt = ft.Text(current_label, size=11, color=_TOGGLE_ON,
                           weight=ft.FontWeight.W_600, no_wrap=True,
                           overflow=ft.TextOverflow.ELLIPSIS)
            btn = _summary_btn(btxt)

            def _open(_ev):
                if not self.page:
                    return
                cur = str(self._ocr_style.get(pref_key, ""))

                def _sel(value: str) -> None:
                    self._ocr_style[pref_key] = value
                    btxt.value = label_of(value)
                    with contextlib.suppress(Exception):
                        btxt.update()
                    if callable(self.on_ocr_style_change):
                        _guarded(lambda v: self.on_ocr_style_change(
                            pref_key, v), value)

                SettingsModal(self.page, title, options, _sel).open(cur)

            btn.on_click = _open
            return btn

        # ── output format (mirrors the chatbox Output Format options) ──
        _fmt_labels = {
            "orig_trans": t("dashboard.translit.fmt.orig_trans"),
            "orig_pinyin_trans": t("dashboard.translit.fmt.orig_read_trans",
                                   system="Pinyin"),
            "pinyin_trans": t("dashboard.translit.fmt.read_trans",
                              system="Pinyin"),
            "pinyin_only": t("dashboard.translit.fmt.read_only",
                             system="Pinyin"),
            "trans_only": t("dashboard.translit.fmt.trans_only"),
        }
        fmt_btn = _modal_row_btn(
            _fmt_labels.get(self._ocr_style.get("ocr_format", "trans_only"),
                            _fmt_labels["trans_only"]),
            t("dashboard.ocr.menu.format"),
            [OptionItem(value=k, label=v, description="", disabled=False)
             for k, v in _fmt_labels.items()],
            "ocr_format", lambda v: _fmt_labels.get(v, v))

        # ── style sub-menu ──
        _color_opts = [("#ff2020", "Red"), ("#2dd4bf", "Teal"),
                       ("#ffffff", "White"), ("#ffd21e", "Yellow"),
                       ("#3b82f6", "Blue"), ("#22c55e", "Green"),
                       ("#ff5df1", "Magenta"), ("#ff8c00", "Orange"),
                       ("#14161a", "Dark"), ("#000000", "Black")]
        _alpha_labels = {"100": "100%", "75": "75%", "50": "50%",
                         "25": "25%",
                         "0": t("dashboard.ocr.opacity.none")}
        _place_labels = {"cover": t("dashboard.ocr.place.cover"),
                         "above": t("dashboard.ocr.place.above")}
        _font_labels = {
            "0": t("dashboard.ocr.font.auto"),
            "-1": t("dashboard.ocr.match_original"),
            "14": t("settings.overlay.desktop.size.option.small"),
            "18": t("settings.overlay.desktop.size.option.medium"),
            "24": t("settings.overlay.desktop.size.option.large"),
            "32": t("settings.overlay.desktop.size.option.xlarge"),
            "44": t("settings.overlay.desktop.size.option.xlarge") + " +",
        }

        def _mk_size_btn(pref_key: str, title: str,
                         labels: dict) -> ft.Container:
            """Size picker button: presets + a Custom… numeric dialog.
            Values are px strings; whatever isn't a preset shows 'N px'."""

            def _label_of(v) -> str:
                s = str(v)
                return labels.get(s, f"{s} px")

            btxt = ft.Text(
                _label_of(self._ocr_style.get(pref_key, 0)),
                size=11, color=_TOGGLE_ON, weight=ft.FontWeight.W_600,
                no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
            btn = _summary_btn(btxt)

            def _apply(px: int) -> None:
                self._ocr_style[pref_key] = str(px)
                btxt.value = _label_of(px)
                with contextlib.suppress(Exception):
                    btxt.update()
                if callable(self.on_ocr_style_change):
                    _guarded(lambda v: self.on_ocr_style_change(
                        pref_key, v), px)

            def _ask_custom() -> None:
                cur = str(self._ocr_style.get(pref_key, 0))
                tf = ft.TextField(
                    value=cur if cur not in ("", "0", "-1") else "24",
                    width=110, autofocus=True, suffix_text="px",
                    keyboard_type=ft.KeyboardType.NUMBER)

                def _ok(_e) -> None:
                    try:
                        px = max(8, min(72, int(str(tf.value).strip())))
                    except Exception:
                        self.page.close(dlg)
                        return
                    self.page.close(dlg)
                    _apply(px)

                dlg = ft.AlertDialog(
                    title=ft.Text(title, size=14),
                    content=tf,
                    actions=[
                        ft.TextButton(t("common.cancel"),
                                      on_click=lambda _e:
                                      self.page.close(dlg)),
                        ft.TextButton("OK", on_click=_ok),
                    ])
                tf.on_submit = _ok
                self.page.open(dlg)

            def _open(_ev) -> None:
                if not self.page:
                    return
                opts = [OptionItem(value=k, label=v, description="",
                                   disabled=False)
                        for k, v in labels.items()]
                opts.append(OptionItem(
                    value="custom", label=t("dashboard.ocr.font.custom"),
                    description="", disabled=False))

                def _sel(value: str) -> None:
                    if value == "custom":
                        _ask_custom()
                        return
                    _apply(int(value))

                SettingsModal(self.page, title, opts, _sel).open(
                    str(self._ocr_style.get(pref_key, 0)))

            btn.on_click = _open
            return btn

        def _mk_font_btn() -> ft.Container:
            return _mk_size_btn("ocr_font_px",
                                t("dashboard.ocr.style.font"), _font_labels)

        _sub_size_labels = {
            "0": t("dashboard.ocr.size.inherit"),
            "14": t("settings.overlay.desktop.size.option.small"),
            "18": t("settings.overlay.desktop.size.option.medium"),
            "24": t("settings.overlay.desktop.size.option.large"),
            "32": t("settings.overlay.desktop.size.option.xlarge"),
        }

        def _color_name(v: str) -> str:
            for hexv, name in _color_opts:
                if hexv == v:
                    return name
            return v

        def _text_color_name(v: str) -> str:
            if v == "auto":
                return t("dashboard.ocr.match_original")
            return _color_name(v)

        def _mk_style_bool(pref_key: str,
                           default_on: bool = True) -> ft.Container:
            cur = str(self._ocr_style.get(
                pref_key, "1" if default_on else "0")) \
                not in ("0", "False", "false")

            def _cb(v: bool) -> None:
                self._ocr_style[pref_key] = "1" if v else "0"
                if callable(self.on_ocr_style_change):
                    _guarded(lambda vv: self.on_ocr_style_change(
                        pref_key, vv), 1 if v else 0)

            return _bool_pill(cur, _cb)

        def _mk_line_color_row(pref_key: str, title: str,
                               inherit_label: str) -> ft.Container:
            def _lbl(v) -> str:
                s = str(v)
                if s == "":
                    return inherit_label
                if s == "auto":
                    return t("dashboard.ocr.match_original")
                return _color_name(s)

            return _mk_row(title, _modal_row_btn(
                _lbl(self._ocr_style.get(pref_key, "")), title,
                [OptionItem(value="", label=inherit_label,
                            description="", disabled=False),
                 OptionItem(value="auto",
                            label=t("dashboard.ocr.match_original"),
                            description="", disabled=False)]
                + [OptionItem(value=h, label=nm, description="",
                              disabled=False) for h, nm in _color_opts],
                pref_key, _lbl))

        def _open_style_menu(_ev) -> None:
            rows = [
                _mk_row(t("dashboard.ocr.style.outline"),
                        _modal_row_btn(
                            _color_name(self._ocr_style.get(
                                "ocr_outline", "#ff2020")),
                            t("dashboard.ocr.style.outline"),
                            [OptionItem(value=h, label=n, description="",
                                        disabled=False)
                             for h, n in _color_opts],
                            "ocr_outline", _color_name), top=8),
                _mk_row(t("dashboard.ocr.style.background"),
                        _modal_row_btn(
                            _color_name(self._ocr_style.get(
                                "ocr_bg", "#14161a")),
                            t("dashboard.ocr.style.background"),
                            [OptionItem(value=h, label=n, description="",
                                        disabled=False)
                             for h, n in _color_opts],
                            "ocr_bg", _color_name)),
                _mk_row(t("dashboard.ocr.style.opacity"),
                        _modal_row_btn(
                            _alpha_labels.get(str(self._ocr_style.get(
                                "ocr_bg_alpha", 100)), "100%"),
                            t("dashboard.ocr.style.opacity"),
                            [OptionItem(value=k, label=v, description="",
                                        disabled=False)
                             for k, v in _alpha_labels.items()],
                            "ocr_bg_alpha",
                            lambda v: _alpha_labels.get(str(v), str(v)))),
                _mk_row(t("dashboard.ocr.style.text"),
                        _modal_row_btn(
                            _text_color_name(self._ocr_style.get(
                                "ocr_text", "#ffffff")),
                            t("dashboard.ocr.style.text"),
                            [OptionItem(value=h, label=n, description="",
                                        disabled=False)
                             for h, n in ([("auto", t(
                                 "dashboard.ocr.match_original"))]
                                 + _color_opts)],
                            "ocr_text", _text_color_name)),
                _mk_row(t("dashboard.ocr.style.placement"),
                        _modal_row_btn(
                            _place_labels.get(self._ocr_style.get(
                                "ocr_place", "cover"), ""),
                            t("dashboard.ocr.style.placement"),
                            [OptionItem(value=k, label=v, description="",
                                        disabled=False)
                             for k, v in _place_labels.items()],
                            "ocr_place",
                            lambda v: _place_labels.get(v, v))),
                _mk_row(t("dashboard.ocr.style.font"), _mk_font_btn()),
                _mk_row(t("dashboard.ocr.size.pinyin"),
                        _mk_size_btn("ocr_size_pinyin",
                                     t("dashboard.ocr.size.pinyin"),
                                     _sub_size_labels)),
                _mk_row(t("dashboard.ocr.size.trans"),
                        _mk_size_btn("ocr_size_trans",
                                     t("dashboard.ocr.size.trans"),
                                     _sub_size_labels)),
                _mk_row(t("dashboard.ocr.size.pronoun"),
                        _mk_size_btn("ocr_size_pronoun",
                                     t("dashboard.ocr.size.pronoun"),
                                     _sub_size_labels)),
                _mk_line_color_row("ocr_color_orig",
                                   t("dashboard.ocr.color.orig"),
                                   t("dashboard.ocr.color.inherit")),
                _mk_line_color_row("ocr_color_trans",
                                   t("dashboard.ocr.color.trans"),
                                   t("dashboard.ocr.color.inherit")),
                _mk_line_color_row("ocr_color_pinyin",
                                   t("dashboard.ocr.color.pinyin"),
                                   t("dashboard.ocr.color.default")),
                _mk_line_color_row("ocr_color_pronoun",
                                   t("dashboard.ocr.color.pronoun"),
                                   t("dashboard.ocr.color.inherit")),
                _mk_row(t("dashboard.ocr.pinyin.tones"),
                        _mk_style_bool("ocr_pinyin_tone")),
                _mk_row(t("dashboard.ocr.pinyin.group"),
                        _mk_style_bool("ocr_pinyin_group")),
                ft.Container(height=6),
            ]
            self._ocr_style_popover_close = self._open_popover_at(
                x + 40, y + 40,
                ft.Container(height=520.0, content=ft.Column(
                    rows, spacing=0, tight=True,
                    scroll=ft.ScrollMode.AUTO,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH)),
                width=280.0, est_height=540.0)

        def _mk_row(label: str, control, top: int = 4) -> ft.Container:
            return _section_row(label, control, top)

        style_btn = ft.Container(
            content=ft.Icon(ft.Icons.PALETTE_OUTLINED, size=15,
                            color=_TEXT_PRIMARY),
            padding=ft.padding.symmetric(horizontal=8, vertical=3),
            border_radius=6,
            border=ft.border.all(1, "#3a3b3f"),
            on_click=_open_style_menu,
        )

        # ── scan activation: TWO independent bind recorders ──
        # Hold = scan while the combo is down; Toggle = a tap flips
        # persistent scanning. Either (or both) may be set; Esc clears.
        # (Legacy single-bind migration happens ONCE in __init__, keyed on
        # the config file — never here, where it re-ran per menu open.)

        # Recorder candidates: letters, digits, F-keys, NUMPAD, mouse 3/4/5.
        # Polled via GetAsyncKeyState — flet's keyboard events never see
        # mouse buttons and mangle numpad keys.
        _REC_VKS: dict[int, str] = {}
        for _c in range(ord("A"), ord("Z") + 1):
            _REC_VKS[_c] = chr(_c)
        for _c in range(ord("0"), ord("9") + 1):
            _REC_VKS[_c] = chr(_c)
        for _i in range(24):  # F1-F24
            _REC_VKS[0x70 + _i] = f"F{_i + 1}"
        for _i in range(10):
            _REC_VKS[0x60 + _i] = f"NUM{_i}"
        _REC_VKS.update({0x6A: "NUMMUL", 0x6B: "NUMADD", 0x6D: "NUMSUB",
                         0x6E: "NUMDEC", 0x6F: "NUMDIV",
                         0x04: "MOUSE3", 0x05: "MOUSE4", 0x06: "MOUSE5",
                         0x20: "SPACE", 0x09: "TAB", 0x0D: "ENTER",
                         0x08: "BACKSPACE",
                         0x26: "UP", 0x28: "DOWN", 0x25: "LEFT",
                         0x27: "RIGHT",
                         0x24: "HOME", 0x23: "END", 0x21: "PGUP",
                         0x22: "PGDN", 0x2D: "INS", 0x2E: "DEL",
                         0x13: "PAUSE", 0x91: "SCROLL",
                         0xBA: "SEMI", 0xBB: "EQUALS", 0xBC: "COMMA",
                         0xBD: "MINUS", 0xBE: "PERIOD", 0xBF: "SLASH",
                         0xC0: "GRAVE", 0xDB: "LBRACKET",
                         0xDC: "BACKSLASH", 0xDD: "RBRACKET",
                         0xDE: "QUOTE"})
        self._bind_btxts = {}

        def _mk_bind_recorder(pref_key: str) -> ft.Container:
            btxt = ft.Text(
                str(self._ocr_style.get(pref_key, "")) or "—",
                size=11, color=_TOGGLE_ON, weight=ft.FontWeight.W_600,
                no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
            )
            btn = _summary_btn(btxt)
            self._bind_btxts[pref_key] = btxt

            def _finish(combo: str) -> None:
                # One combo can't drive two mechanisms: recording a bind
                # already on ANY other slot steals it (clears it there).
                _all_keys = ("scan_bind", "scan_bind_toggle",
                             "unfiltered_bind", "unfiltered_bind_toggle")
                if combo:
                    for other in _all_keys:
                        if other == pref_key:
                            continue
                        if str(self._ocr_style.get(other, "")) == combo:
                            self._ocr_style[other] = ""
                            ob = self._bind_btxts.get(other)
                            if ob is not None:
                                ob.value = "—"
                                with contextlib.suppress(Exception):
                                    ob.update()
                self._ocr_style[pref_key] = combo
                btxt.value = combo or "—"
                with contextlib.suppress(Exception):
                    btxt.update()
                if callable(self.on_ocr_style_change):
                    # Write ALL bind keys: scan_bind_toggle presence flips
                    # the overlay to dual-bind config (ends the legacy
                    # scan_mode migration), and steals must persist too.
                    for k2 in _all_keys:
                        _guarded(lambda v, kk=k2:
                                 self.on_ocr_style_change(kk, v),
                                 str(self._ocr_style.get(k2, "")))

            def _record(_ev) -> None:
                if not self.page:
                    return
                btxt.value = "…"
                with contextlib.suppress(Exception):
                    btxt.update()
                gen = getattr(self, "_bind_rec_gen", 0) + 1
                self._bind_rec_gen = gen

                async def _poll() -> None:
                    import asyncio
                    import ctypes
                    import time as _time
                    u32 = ctypes.windll.user32
                    deadline = _time.monotonic() + 15.0
                    # Wait until every candidate is UP so the click that
                    # armed the recorder (or a lingering Enter) isn't read.
                    while _time.monotonic() < deadline \
                            and gen == getattr(self, "_bind_rec_gen", 0):
                        if not any(u32.GetAsyncKeyState(vk) & 0x8000
                                   for vk in _REC_VKS) \
                                and not (u32.GetAsyncKeyState(0x1B)
                                         & 0x8000):
                            break
                        await asyncio.sleep(0.02)
                    combo = None
                    while _time.monotonic() < deadline \
                            and gen == getattr(self, "_bind_rec_gen", 0):
                        if u32.GetAsyncKeyState(0x1B) & 0x8000:  # Esc
                            combo = ""
                            break
                        hit = next(
                            (nm for vk, nm in _REC_VKS.items()
                             if u32.GetAsyncKeyState(vk) & 0x8000), None)
                        if hit is not None:
                            parts = []
                            if u32.GetAsyncKeyState(0x11) & 0x8000:
                                parts.append("CTRL")
                            if u32.GetAsyncKeyState(0x12) & 0x8000:
                                parts.append("ALT")
                            if u32.GetAsyncKeyState(0x10) & 0x8000:
                                parts.append("SHIFT")
                            parts.append(hit)
                            combo = "+".join(parts)
                            break
                        await asyncio.sleep(0.02)
                    if gen != getattr(self, "_bind_rec_gen", 0):
                        return  # a newer recording superseded this one
                    if combo is None:  # timed out — keep the old bind
                        combo = str(self._ocr_style.get(pref_key, ""))
                    _finish(combo)

                with contextlib.suppress(Exception):
                    self.page.run_task(_poll)

            btn.on_click = _record
            return btn

        hold_bind_btn = _mk_bind_recorder("scan_bind")
        toggle_bind_btn = _mk_bind_recorder("scan_bind_toggle")
        unf_hold_bind_btn = _mk_bind_recorder("unfiltered_bind")
        unf_toggle_bind_btn = _mk_bind_recorder("unfiltered_bind_toggle")

        # Region border visibility (the dashed rectangle on screen).
        _border_on = str(self._ocr_style.get(
            "ocr_region_border", "1")) not in ("0", "False", "false")
        border_icon = ft.Icon(
            ft.Icons.VISIBILITY if _border_on else ft.Icons.VISIBILITY_OFF,
            size=15, color=_TOGGLE_ON if _border_on else _TEXT_FAINT)
        border_btn = ft.Container(
            content=border_icon,
            padding=ft.padding.symmetric(horizontal=6, vertical=3),
            border_radius=6,
            border=ft.border.all(1, "#3a3b3f"),
        )

        def _toggle_border(_ev) -> None:
            cur = str(self._ocr_style.get(
                "ocr_region_border", "1")) not in ("0", "False", "false")
            new = not cur
            self._ocr_style["ocr_region_border"] = "1" if new else "0"
            border_icon.name = (ft.Icons.VISIBILITY if new
                                else ft.Icons.VISIBILITY_OFF)
            border_icon.color = _TOGGLE_ON if new else _TEXT_FAINT
            with contextlib.suppress(Exception):
                border_icon.update()
            if callable(self.on_ocr_style_change):
                _guarded(lambda v: self.on_ocr_style_change(
                    "ocr_region_border", v), 1 if new else 0)

        border_btn.on_click = _toggle_border

        # Live scan status: the overlay writes ocr_state.json on every
        # scan on/off transition (plus a 2s heartbeat) — poll it while
        # the menu is open so the toggle bind visibly works.
        _status_txt = ft.Text("…", size=11, weight=ft.FontWeight.W_600,
                              color=_TEXT_FAINT, no_wrap=True)
        _status_pill = ft.Container(
            content=_status_txt,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6, bgcolor="#22252b",
            border=ft.border.all(1, "#3a3b3f"))

        def _read_scan_state():
            try:
                import json as _json
                import os as _os
                import time as _time
                p = _os.path.join(_os.path.expanduser("~"), "AppData",
                                  "Local", "puripuly-heart",
                                  "ocr_state.json")
                with open(p, encoding="utf-8") as fh:
                    st = _json.load(fh)
                if _time.time() - float(st.get("ts", 0)) > 8:
                    return None  # stale — overlay not running
                return st
            except Exception:
                return None

        async def _poll_state() -> None:
            import asyncio
            gen = getattr(self, "_ocr_state_gen", 0) + 1
            self._ocr_state_gen = gen
            while gen == getattr(self, "_ocr_state_gen", 0):
                st = _read_scan_state()
                if st is None:
                    _status_txt.value = t("dashboard.ocr.state.off")
                    _status_txt.color = _TEXT_FAINT
                elif st.get("scan"):
                    _status_txt.value = t("dashboard.ocr.state.on")
                    _status_txt.color = _TOGGLE_ON
                else:
                    _status_txt.value = t("dashboard.ocr.state.idle")
                    _status_txt.color = _TEXT_FAINT
                try:
                    _status_txt.update()
                except Exception:
                    return  # menu closed — control detached
                await asyncio.sleep(0.5)

        with contextlib.suppress(Exception):
            self.page.run_task(_poll_state)

        content = ft.Container(
            content=ft.Column(
                [
                    _row_tt("dashboard.ocr.state",
                                 _status_pill, top=8),
                    _row_tt("dashboard.ocr.menu.window",
                                 _win_btn),
                    _row_tt("dashboard.ocr.menu.foreign_only",
                                 _bool_pill(self._ocr_foreign_only,
                                            _on_foreign)),
                    _row_tt("dashboard.ocr.menu.translate",
                                 _bool_pill(self._ocr_translate,
                                            _on_translate)),
                    _row_tt("dashboard.ocr.menu.xlat_model",
                                 _svc_btn),
                    _row_tt("dashboard.ocr.menu.format", fmt_btn),
                    _row_tt("dashboard.ocr.menu.style", style_btn),
                    _row_tt("dashboard.ocr.scan.hold_bind",
                                 hold_bind_btn),
                    _row_tt("dashboard.ocr.scan.toggle_bind",
                                 toggle_bind_btn),
                    _section_row(t("dashboard.ocr.scan.unfiltered_hold_bind"),
                                 unf_hold_bind_btn,
                                 tooltip=t(
                                     "dashboard.ocr.scan.unfiltered.tooltip")),
                    _section_row(
                        t("dashboard.ocr.scan.unfiltered_toggle_bind"),
                        unf_toggle_bind_btn,
                        tooltip=t("dashboard.ocr.scan.unfiltered.tooltip")),
                    _row_tt("dashboard.ocr.menu.bubbles_only",
                                 _bool_pill(self._ocr_bubbles_only,
                                            _on_bubbles)),
                    _row_tt("dashboard.ocr.menu.ignore_names",
                                 _bool_pill(self._ocr_ignore_names,
                                            _on_names)),
                    _row_tt("dashboard.ocr.menu.ignore_pronouns",
                                 _bool_pill(self._ocr_ignore_pronouns,
                                            _on_pronouns)),
                    _row_tt("dashboard.ocr.menu.ignore_groups",
                                 _mk_style_bool("ignore_groups")),
                    _row_tt("dashboard.ocr.menu.prewarm",
                                 _bool_pill(self._ocr_prewarm, _on_prewarm)),
                    _row_tt("dashboard.ocr.menu.log_chat",
                                 _bool_pill(self.ocr_log_chat, _on_log)),
                    _row_tt("dashboard.ocr.menu.debug_shots",
                                 _mk_style_bool("debug_shots",
                                                default_on=False)),
                    _row_tt(
                        "dashboard.ocr.menu.region_set",
                        ft.Row([set_btn, border_btn, lock_pill], spacing=6,
                               tight=True),
                    ),
                    # Remove the ~340MB OCR module to free disk space; the
                    # download dialog reacquires it on the next OCR use.
                    ft.Container(
                        content=ft.Text(t("dashboard.ocr.menu.remove_module"),
                                        size=11, color="#c76b6b"),
                        padding=ft.padding.symmetric(horizontal=8, vertical=6),
                        border_radius=6,
                        on_click=self._on_ocr_remove_module_click,
                        tooltip=t("dashboard.ocr.menu.remove_module.tooltip"),
                    ),
                    ft.Container(height=6),
                ],
                spacing=0,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )
        self._ocr_popover_close = self._open_popover_at(
            x, y, content, width=300.0, est_height=760.0)

    def _on_ocr_remove_module_click(self, _e=None) -> None:
        if callable(self._ocr_popover_close):
            with contextlib.suppress(Exception):
                self._ocr_popover_close()
        if callable(self.on_ocr_remove_module):
            self.on_ocr_remove_module()

    def _set_ocr_region(self) -> None:
        if not self._ocr_on:
            self._on_ocr_btn_click(None)
        if callable(self.on_ocr_region_set):
            self.on_ocr_region_set()

    def _toggle_ocr_foreign_only(self) -> None:
        self._ocr_foreign_only = not self._ocr_foreign_only
        if callable(self.on_ocr_foreign_change):
            self.on_ocr_foreign_change(self._ocr_foreign_only)

    def _toggle_ocr_ignore_names(self) -> None:
        self._ocr_ignore_names = not self._ocr_ignore_names
        if callable(self.on_ocr_ignore_names_change):
            self.on_ocr_ignore_names_change(self._ocr_ignore_names)

    def _toggle_ocr_ignore_pronouns(self) -> None:
        self._ocr_ignore_pronouns = not self._ocr_ignore_pronouns
        if callable(self.on_ocr_ignore_pronouns_change):
            self.on_ocr_ignore_pronouns_change(self._ocr_ignore_pronouns)

    def _toggle_ocr_translate(self) -> None:
        self._ocr_translate = not self._ocr_translate
        if callable(self.on_ocr_translate_change):
            self.on_ocr_translate_change(self._ocr_translate)

    def _toggle_ocr_log_chat(self) -> None:
        self.ocr_log_chat = not self.ocr_log_chat
        self._save_ocr_pref("log_chat", self.ocr_log_chat)

    @staticmethod
    def _save_ocr_pref(key: str, value) -> None:
        with contextlib.suppress(Exception):
            from puripuly_heart.ocr.manager import save_ocr_pref

            save_ocr_pref(key, value)

    def _toggle_ocr_vrchat_only(self) -> None:
        self._ocr_vrchat_only = not self._ocr_vrchat_only
        self._save_ocr_pref("vrchat_only", self._ocr_vrchat_only)
        if callable(self.on_ocr_scope_change):
            self.on_ocr_scope_change(self._ocr_vrchat_only)

    def _toggle_ocr_bubbles(self) -> None:
        self._ocr_bubbles_only = not self._ocr_bubbles_only
        if callable(self.on_ocr_bubbles_change):
            self.on_ocr_bubbles_change(self._ocr_bubbles_only)

    def _toggle_ocr_prewarm(self) -> None:
        self._ocr_prewarm = not self._ocr_prewarm
        if callable(self.on_ocr_prewarm_change):
            self.on_ocr_prewarm_change(self._ocr_prewarm)

    def _toggle_ocr_region(self) -> None:
        # Selecting a region needs the overlay running — flip the pill on
        # first so its visual state matches reality.
        if not self._ocr_on:
            self._on_ocr_btn_click(None)
        if callable(self.on_ocr_region_toggle):
            self.on_ocr_region_toggle()

    def _toggle_peer_translation(self) -> None:
        self._peer_showing_error = False
        enabled = True
        if self._overlay_peer_contract is not None:
            enabled = not self._overlay_peer_contract.peer.intent_enabled
        if self.on_toggle_peer_translation:
            self.on_toggle_peer_translation(enabled)

    # ── State sync ───────────────────────────────────────────────────────────

    def _sync_stt_button_state(self) -> None:
        self._row_stt.set_state(self.is_stt_on, warning=self._stt_showing_warning, error=self._stt_showing_error)
        if hasattr(self, "_mini_stt_btn"):
            self._mini_stt_btn.set_state(self.is_stt_on, warning=self._stt_showing_warning, error=self._stt_showing_error)
        if hasattr(self, "_vrc_mute_sync_btn"):
            self._refresh_vrc_mute_sync_btn()
        # Mic state gates the model-loading banner (hidden while all channels
        # are off), so re-evaluate the notice like the peer path does.
        with contextlib.suppress(Exception):
            self._sync_notice()

    def _sync_translation_button_state(self) -> None:
        self._row_trans.set_state(self.is_translation_on, warning=self._translation_showing_warning)
        if hasattr(self, "_mini_trans_btn"):
            self._mini_trans_btn.set_state(self.is_translation_on, warning=self._translation_showing_warning)

    def _sync_overlay_peer_buttons(self) -> None:
        contract = self._overlay_peer_contract
        if contract is None:
            self._row_peer.set_state(False)
            self._row_overlay.set_state(False)
            self._sync_overlay_header_btn(active=False)
            self._sync_notice()
            return
        peer_on = contract.peer.state == "on"
        peer_warn = contract.peer.state == "warning"
        self._row_peer.set_state(peer_on, warning=peer_warn, error=self._peer_showing_error)
        if hasattr(self, "_mini_peer_btn"):
            self._mini_peer_btn.set_state(peer_on, warning=peer_warn, error=self._peer_showing_error)
        overlay_on = contract.overlay.state == "on"
        overlay_warn = contract.overlay.state == "warning"
        self._row_overlay.set_state(overlay_on, warning=overlay_warn)
        self._sync_overlay_header_btn(active=overlay_on, warning=overlay_warn)
        self._sync_notice()

    def _sync_overlay_header_btn(self, *, active: bool, warning: bool = False) -> None:
        btn = self._overlay_header_btn
        if btn is None:
            return
        if warning:
            color = "#e0a030"
            bg = "#332800"
            border = ft.border.all(1, "#e0a030")
        elif active:
            color = _TOGGLE_ON
            bg = "#1a2e2a"
            border = ft.border.all(1, _TOGGLE_ON)
        else:
            color = _TEXT_FAINT
            bg = ft.Colors.TRANSPARENT
            border = ft.border.all(1, "#3a3b3f")
        btn.bgcolor = bg
        btn.border = border
        self._overlay_header_text.color = color
        # The VR/PC chip and lock state only make sense while the overlay is on.
        self._overlay_active = bool(active)
        self._refresh_overlay_mode_chip()
        self._refresh_overlay_lock_icon()
        try:
            btn.update()
        except Exception:
            pass

    # Compatibility aliases used by controller
    @property
    def stt_button(self): return self._row_stt
    @property
    def peer_button(self): return self._row_peer
    @property
    def trans_button(self): return self._row_trans
    @property
    def overlay_button(self): return self._row_overlay

    @property
    def single_turn_mode(self) -> bool:
        return self._single_turn_mode_backing

    @single_turn_mode.setter
    def single_turn_mode(self, value: bool) -> None:
        self._single_turn_mode_backing = bool(value)
        # Keep the right-click menu button in sync so the button always reflects
        # the persisted setting, not just the hardcoded __init__ default.
        if hasattr(self, "_overlay_single_turn"):
            self._overlay_single_turn = bool(value)

    # ── STT toggle ───────────────────────────────────────────────────────────

    def _toggle_stt(self):
        self._stt_showing_error = False
        if self.is_stt_on:
            self.is_stt_on = False
            self._stt_showing_warning = False
        elif self._stt_showing_warning:
            self._stt_showing_warning = False
        elif self.stt_needs_key:
            self._stt_showing_warning = True
            self.set_display_text(t("dashboard.warn_stt_key"))
        else:
            self.is_stt_on = True
            self._stt_showing_warning = False
        self._sync_stt_button_state()
        if self.on_toggle_stt:
            self.on_toggle_stt(self.is_stt_on)

    # ── Translation toggle ───────────────────────────────────────────────────

    def _toggle_translation(self):
        if self.is_translation_on:
            self.is_translation_on = False
            self._translation_showing_warning = False
        elif self._translation_showing_warning:
            self._translation_showing_warning = False
        elif self.translation_needs_key:
            self._translation_showing_warning = True
            self.set_display_text(t("dashboard.warn_llm_key"))
        else:
            self.is_translation_on = True
            self._translation_showing_warning = False
        self._sync_translation_button_state()
        self.is_power_on = self.is_translation_on
        if self.on_toggle_translation:
            self.on_toggle_translation(self.is_translation_on)

    # ── Chat ─────────────────────────────────────────────────────────────────

    def _on_overlay_lock_click(self, e) -> None:
        new_locked = not self._overlay_locked
        self.set_overlay_locked(new_locked)
        if callable(self.on_overlay_lock_change):
            self.on_overlay_lock_change(new_locked)

    def set_overlay_locked(self, locked: bool) -> None:
        self._overlay_locked = locked
        self._refresh_overlay_lock_icon()

    def _refresh_overlay_lock_icon(self) -> None:
        icon = getattr(self, "_overlay_lock_icon", None)
        side = getattr(self, "_overlay_lock_side", None)
        if icon is None:
            return
        locked = self._overlay_locked
        icon.name = ft.Icons.LOCK if locked else ft.Icons.LOCK_OPEN
        # Only light the lock teal while the overlay is actually on; when it's off the
        # whole button is dim, so a glowing lock looks out of place.
        if locked and self._overlay_active:
            icon.color = _TOGGLE_ON
        else:
            icon.color = _TEXT_FAINT
        if side is not None:
            side.tooltip = (
                t("dashboard.overlay.lock.locked") if locked else t("dashboard.overlay.lock.unlocked")
            )
        try:
            icon.update()
        except Exception:
            pass

    def set_overlay_mode(self, target: str | None) -> None:
        """Record which overlay target is resolved (steamvr/desktop) and refresh chip."""
        self._overlay_mode_value = (target or "").strip().lower() or None
        self._refresh_overlay_mode_chip()

    def _refresh_overlay_mode_chip(self) -> None:
        """Show the VR/PC chip only while the overlay is on."""
        chip = getattr(self, "_overlay_mode_chip", None)
        label = getattr(self, "_overlay_mode_text", None)
        if chip is None or label is None:
            return
        normalized = self._overlay_mode_value if self._overlay_active else None
        if normalized == "steamvr":
            label.value = t("dashboard.overlay.mode.vr_short")
            label.color = "#7fd4c4"
            chip.bgcolor = "#1f3a35"
            chip.tooltip = t("dashboard.overlay.mode.vr_tooltip")
            chip.visible = True
        elif normalized == "desktop":
            label.value = t("dashboard.overlay.mode.desktop_short")
            label.color = "#a8b0bd"
            chip.bgcolor = "#33343a"
            chip.tooltip = t("dashboard.overlay.mode.desktop_tooltip")
            chip.visible = True
        else:
            chip.visible = False
        try:
            chip.update()
        except Exception:
            pass

    def _on_overlay_right_click(self, e) -> None:
        # ── helpers ──────────────────────────────────────────────────────────
        def _pill(label: str, active: bool, on_click, expand: bool = False) -> ft.Container:
            return ft.Container(
                content=ft.Text(
                    label, size=11,
                    color=_TOGGLE_ON if active else _TEXT_MUTED,
                    weight=ft.FontWeight.W_600,
                    text_align=ft.TextAlign.CENTER,
                    no_wrap=True,
                ),
                on_click=on_click,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=6,
                expand=expand,
                bgcolor="#1a2e2a" if active else ft.Colors.TRANSPARENT,
                border=ft.border.all(1, _TOGGLE_ON if active else "#3a3b3f"),
            )

        def _section_row(label: str, control: Any, top: int = 4,
                         tooltip: str | None = None) -> ft.Container:
            _row: list[Any] = [ft.Text(label, size=11, color=_TEXT_MUTED)]
            if tooltip:
                _row.append(ft.Container(
                    content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11, color=_TEXT_FAINT),
                    tooltip=tooltip,
                    padding=ft.padding.only(left=3),
                ))
            _row.append(ft.Container(expand=True))
            _row.append(control)
            return ft.Container(
                content=ft.Row(
                    _row, spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.padding.only(left=10, right=10, top=top, bottom=2),
            )


        def _row_tt(key: str, control, top: int = 4) -> ft.Container:
            # Labeled row with an auto info tooltip from '<key>.tooltip'.
            return _section_row(t(key), control, top=top,
                                tooltip=t(key + ".tooltip"))

        def _bool_pill(state_ref: list[bool], on_change) -> ft.Container:
            lbl = ft.Text(
                t("settings.option.on") if state_ref[0] else t("settings.option.off"),
                size=11,
                color=_TOGGLE_ON if state_ref[0] else _TEXT_FAINT,
                weight=ft.FontWeight.W_600,
            )
            box = ft.Container(
                content=lbl,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=6,
                border=ft.border.all(1, _TOGGLE_ON if state_ref[0] else "#3a3b3f"),
            )
            def _click(_ev, _r=state_ref, _l=lbl, _b=box):
                _r[0] = not _r[0]
                _l.value = t("settings.option.on") if _r[0] else t("settings.option.off")
                _l.color = _TOGGLE_ON if _r[0] else _TEXT_FAINT
                _b.border = ft.border.all(1, _TOGGLE_ON if _r[0] else "#3a3b3f")
                try: _l.update(); _b.update()
                except Exception: pass
                on_change(_r[0])
            box.on_click = _click
            return box

        # ── opacity slider ────────────────────────────────────────────────────
        alpha_pct = ft.Text(
            f"{int(round(self._overlay_background_alpha * 100))}%",
            size=11, color=_TEXT_MUTED, width=32, text_align=ft.TextAlign.RIGHT,
        )
        slider = ft.Slider(
            value=self._overlay_background_alpha, min=0.0, max=1.0, divisions=100,
            active_color=_TOGGLE_ON, inactive_color=_TOGGLE_OFF, thumb_color=_TOGGLE_ON,
            expand=True,
        )
        def _on_slider(ev):
            alpha = round(float(ev.control.value), 2)
            self._overlay_background_alpha = alpha
            alpha_pct.value = f"{int(round(alpha * 100))}%"
            try: alpha_pct.update()
            except Exception: pass
            if callable(self.on_overlay_transparency_change):
                self.on_overlay_transparency_change(alpha)
        slider.on_change = _on_slider

        # ── mode pills (Auto / VR / Desktop) ─────────────────────────────────
        def _current_mode() -> str:
            return "auto" if self._overlay_auto_switch else self._overlay_target_pref

        mode_pills: list[Any] = []

        def _make_mode_pill(mode: str, label: str):
            pill_ref: list[Any] = [None]

            def _click(_ev, _m=mode):
                if _current_mode() == _m:
                    return
                if _m == "auto":
                    self._overlay_auto_switch = True
                else:
                    self._overlay_auto_switch = False
                    self._overlay_target_pref = _m
                if callable(self.on_overlay_mode_select):
                    self.on_overlay_mode_select(_m)
                _close = getattr(self, "_overlay_popover_close", None)
                if callable(_close):
                    _close()

            pil = _pill(label, _current_mode() == mode, _click, expand=True)
            pill_ref[0] = pil
            return pil

        _mode_row = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text(t("dashboard.overlay.mode.label"), size=10, color=_TEXT_FAINT),
                    ft.Container(
                        content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11,
                                        color=_TEXT_FAINT),
                        tooltip=t("dashboard.overlay.mode.label.tooltip"),
                        padding=ft.padding.only(left=3),
                    ),
                ], spacing=0, tight=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([
                    _make_mode_pill("auto",    t("dashboard.overlay.mode.auto_option")),
                    _make_mode_pill("steamvr", t("dashboard.overlay.mode.vr_option")),
                    _make_mode_pill("desktop", t("dashboard.overlay.mode.desktop_option")),
                ], spacing=4),
            ], spacing=4, tight=True),
            padding=ft.padding.only(left=10, right=10, top=8, bottom=4),
        )

        # ── single-turn ───────────────────────────────────────────────────────
        _st_ref = [self._overlay_single_turn]
        def _on_single(val: bool):
            self._overlay_single_turn = val
            if callable(self.on_overlay_single_turn_change):
                self.on_overlay_single_turn_change(val)
        _single_row = _row_tt(
            "dashboard.overlay.single_turn.label",
            _bool_pill(_st_ref, _on_single),
        )

        # ── display: button showing summary + inline checkbox expansion ─────────
        _disp_spec = [
            ("dashboard.overlay.show_original",     "_overlay_show_original",     "show_peer_original",  "orig"),
            ("dashboard.overlay.show_translation",  "_overlay_show_translation",  "show_translation",    "trans"),
            ("dashboard.overlay.show_romanization", "_overlay_show_romanization", "show_romanization",   "latin"),
        ]

        def _disp_summary() -> str:
            parts = [short for _, attr, _, short in _disp_spec if getattr(self, attr)]
            return " + ".join(parts) if parts else t("settings.option.off")

        _disp_btn_text = ft.Text(
            _disp_summary(), size=11, color=_TOGGLE_ON,
            weight=ft.FontWeight.W_600, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
        )
        _disp_btn = ft.Container(
            content=_disp_btn_text,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6,
            bgcolor="#1a2e2a",
            border=ft.border.all(1, _TOGGLE_ON),
        )

        # Build inline checkbox rows (hidden until button clicked)
        _chk_rows: list[Any] = []
        for key, attr, field_name, _short in _disp_spec:
            state = [getattr(self, attr)]
            _lbl = ft.Text(t(key), size=12, color=_TEXT_PRIMARY, expand=True)
            _chk_icon = ft.Icon(
                ft.Icons.CHECK_BOX if state[0] else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                size=15, color=_TOGGLE_ON if state[0] else _TEXT_FAINT,
            )
            def _on_row(_ev, _attr=attr, _fn=field_name, _s=state, _ic=_chk_icon):
                _s[0] = not _s[0]
                setattr(self, _attr, _s[0])
                _ic.name = ft.Icons.CHECK_BOX if _s[0] else ft.Icons.CHECK_BOX_OUTLINE_BLANK
                _ic.color = _TOGGLE_ON if _s[0] else _TEXT_FAINT
                _disp_btn_text.value = _disp_summary()
                _disp_btn.bgcolor = "#1a2e2a" if any(
                    getattr(self, a) for _, a, _, _sh in _disp_spec
                ) else ft.Colors.TRANSPARENT
                try:
                    _ic.update(); _disp_btn_text.update(); _disp_btn.update()
                except Exception: pass
                if callable(self.on_overlay_display_toggle):
                    self.on_overlay_display_toggle(_fn, _s[0])
            _chk_rows.append(ft.Container(
                content=ft.Row([_chk_icon, _lbl], spacing=8,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.padding.only(left=18, right=10, top=6, bottom=6),
                border_radius=5,
                on_click=_on_row,
                on_hover=lambda e: (
                    setattr(e.control, "bgcolor", "#2a3040" if e.data == "true" else ft.Colors.TRANSPARENT)
                    or (e.control.update() if e.control.page else None)
                ),
            ))

        _disp_rows_col = ft.Column(_chk_rows, spacing=0, tight=True, visible=False)

        _disp_expanded = [False]
        def _toggle_disp(_ev):
            _disp_expanded[0] = not _disp_expanded[0]
            _disp_rows_col.visible = _disp_expanded[0]
            try: _disp_rows_col.update()
            except Exception: pass

        _disp_btn.on_click = _toggle_disp
        _disp_pill_row = ft.Column([
            _row_tt("dashboard.overlay.options.display", _disp_btn),
            _disp_rows_col,
        ], spacing=0, tight=True)

        # ── size: button showing current preset + inline radio expansion ────────
        from puripuly_heart.config.settings import DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER

        def _size_label(preset: str) -> str:
            return t(f"settings.overlay.desktop.size.option.{preset}", default=preset)

        _size_btn_text = ft.Text(
            _size_label(self._overlay_size_preset), size=11, color=_TOGGLE_ON,
            weight=ft.FontWeight.W_600, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
        )
        _size_btn = ft.Container(
            content=_size_btn_text,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6,
            bgcolor="#1a2e2a",
            border=ft.border.all(1, _TOGGLE_ON),
        )

        _size_state = [self._overlay_size_preset]
        _size_icons: dict[str, Any] = {}
        _size_rows: list[Any] = []
        for _preset in DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER:
            _active = _preset == _size_state[0]
            _radio_icon = ft.Icon(
                ft.Icons.RADIO_BUTTON_CHECKED if _active else ft.Icons.RADIO_BUTTON_UNCHECKED,
                size=15, color=_TOGGLE_ON if _active else _TEXT_FAINT,
            )
            _size_icons[_preset] = _radio_icon
            _row_lbl = ft.Text(_size_label(_preset), size=12, color=_TEXT_PRIMARY, expand=True)

            def _on_size_row(_ev, _p=_preset):
                if _size_state[0] == _p:
                    return
                _size_state[0] = _p
                self._overlay_size_preset = _p
                for _k, _ic in _size_icons.items():
                    _sel = _k == _p
                    _ic.name = (
                        ft.Icons.RADIO_BUTTON_CHECKED if _sel
                        else ft.Icons.RADIO_BUTTON_UNCHECKED
                    )
                    _ic.color = _TOGGLE_ON if _sel else _TEXT_FAINT
                _size_btn_text.value = _size_label(_p)
                try:
                    _size_btn_text.update()
                    for _ic in _size_icons.values():
                        _ic.update()
                except Exception:
                    pass
                if callable(self.on_overlay_size_select):
                    self.on_overlay_size_select(_p)

            _size_rows.append(ft.Container(
                content=ft.Row([_radio_icon, _row_lbl], spacing=8,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.padding.only(left=18, right=10, top=6, bottom=6),
                border_radius=5,
                on_click=_on_size_row,
                on_hover=lambda e: (
                    setattr(e.control, "bgcolor", "#2a3040" if e.data == "true" else ft.Colors.TRANSPARENT)
                    or (e.control.update() if e.control.page else None)
                ),
            ))

        _size_rows_col = ft.Column(_size_rows, spacing=0, tight=True, visible=False)
        _size_expanded = [False]

        def _toggle_size(_ev):
            _size_expanded[0] = not _size_expanded[0]
            _size_rows_col.visible = _size_expanded[0]
            try: _size_rows_col.update()
            except Exception: pass

        _size_btn.on_click = _toggle_size
        _size_pill_row = ft.Column([
            _row_tt("dashboard.overlay.options.size", _size_btn),
            _size_rows_col,
        ], spacing=0, tight=True)

        # ── show voice / show text ────────────────────────────────────────────
        _v_ref = [self._self_in_overlay]
        def _on_voice(val: bool):
            self._self_in_overlay = val
            if callable(self.on_self_in_overlay_toggle):
                self.on_self_in_overlay_toggle(val)
        _voice_row = _row_tt("dashboard.overlay.show_voice", _bool_pill(_v_ref, _on_voice))

        _tx_ref = [self._typed_in_overlay]
        def _on_typed(val: bool):
            self._typed_in_overlay = val
            if callable(self.on_typed_in_overlay_toggle):
                self.on_typed_in_overlay_toggle(val)
        _text_row = _row_tt("dashboard.overlay.show_text", _bool_pill(_tx_ref, _on_typed))

        # ── divider helper ────────────────────────────────────────────────────
        def _div() -> ft.Container:
            return ft.Container(height=1, bgcolor="#3a3b3f",
                                margin=ft.margin.symmetric(vertical=3, horizontal=6))

        _, y = self._tap_xy(e)
        content = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.TUNE, size=13, color=_TEXT_MUTED),
                            ft.Text(t("dashboard.overlay.options.title"), size=12,
                                    color=_TEXT_MUTED, weight=ft.FontWeight.W_600),
                        ], spacing=6, tight=True),
                        padding=ft.padding.only(left=10, right=10, top=8, bottom=0),
                    ),
                    _mode_row,
                    _div(),
                    ft.Container(
                        content=ft.Row([slider, alpha_pct], spacing=6,
                                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        padding=ft.padding.only(left=10, right=10, top=2, bottom=2),
                    ),
                    # Two-turn mode disabled — the single/two-turn toggle is hidden.
                    # _div(),
                    # _single_row,
                    _div(),
                    _disp_pill_row,
                    _div(),
                    _size_pill_row,
                    _div(),
                    _voice_row,
                    _text_row,
                    ft.Container(height=4),
                ],
                spacing=0,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )
        self._overlay_popover_close = self._open_popover_at(
            0.0, y, content, width=248.0, right_side=True,
            est_height=440.0,
        )

    def set_overlay_background_alpha(self, alpha: float) -> None:
        self._overlay_background_alpha = max(0.0, min(1.0, float(alpha)))

    def set_overlay_size_preset(self, size_preset: str) -> None:
        """Keep the dashboard's Size submenu in sync with the active preset.

        Called when the size changes from Settings (or anywhere else) so the
        right-click menu always opens showing the real current size.
        """
        if isinstance(size_preset, str) and size_preset:
            self._overlay_size_preset = size_preset

    def _open_popover_at(self, x: float, y: float, content: ft.Control,
                         width: float | None = None, clamp_width: float | None = None,
                         right_side: bool = False,
                         est_height: float | None = None):
        """Show an arbitrary popover panel anchored near page coordinates (x, y).

        width=None lets the panel size tightly to its content (menus). The panel is
        clamped to the window so it never gets clipped at an edge. Returns close().
        right_side=True anchors to the right edge of the page (ignores x), so the
        popup never overflows when the window is narrow.
        est_height: approximate content height — tall menus anchor upward to fit,
        and when the WINDOW is shorter than the menu the panel is capped to the
        window height and scrolls instead of getting cut off at the bottom.
        """
        if not self.page:
            return lambda: None
        holder: dict = {}

        def _close() -> None:
            try:
                self.page.overlay.remove(holder["root"])
                self.page.update()
            except Exception:
                pass

        margin = 8.0
        page_h = float(getattr(self.page, "height", 0) or 0)
        top = y
        if page_h:
            top = min(top, max(margin, page_h - margin - 56.0))
        top = max(margin, top)
        panel_height: float | None = None
        if est_height and page_h:
            avail = page_h - 2 * margin
            if est_height >= avail:
                # Window shorter than the menu: pin to the top, cap the
                # panel to the window and let the content scroll.
                top = margin
                panel_height = avail
                content = ft.Column([content], scroll=ft.ScrollMode.AUTO,
                                    tight=True)
            else:
                # Fits — but anchor upward so the bottom never clips.
                top = max(margin, min(top, page_h - margin - est_height))

        if right_side:
            panel = ft.Container(
                content=content,
                width=width,
                height=panel_height,
                bgcolor="#26272b",
                border_radius=8,
                border=ft.border.all(1, "#3a3b3f"),
                padding=5,
                right=margin,
                top=top,
                shadow=ft.BoxShadow(blur_radius=14, spread_radius=1, color="#000000aa"),
            )
        else:
            cw = clamp_width if clamp_width is not None else (width if width is not None else 240.0)
            page_w = float(getattr(self.page, "width", 0) or 0)
            left = x
            if page_w and (left + cw + margin) > page_w:
                left = page_w - cw - margin
            left = max(margin, left)
            panel = ft.Container(
                content=content,
                width=width,
                height=panel_height,
                bgcolor="#26272b",
                border_radius=8,
                border=ft.border.all(1, "#3a3b3f"),
                padding=5,
                left=left,
                top=top,
                shadow=ft.BoxShadow(blur_radius=14, spread_radius=1, color="#000000aa"),
            )
        backdrop = ft.GestureDetector(
            on_tap=lambda _e: _close(),
            on_secondary_tap=lambda _e: _close(),
            content=ft.Container(expand=True, bgcolor=ft.Colors.TRANSPARENT),
        )
        root = ft.Stack([backdrop, panel], expand=True)
        holder["root"] = root
        self.page.overlay.append(root)
        # Close if the window is resized/moved so the popup doesn't get stranded.
        _prev_resize = getattr(self.page, "on_resized", None)

        def _on_resize(_ev):
            _close()
            self.page.on_resized = _prev_resize
            if callable(_prev_resize):
                _prev_resize(_ev)

        self.page.on_resized = _on_resize
        self.page.update()
        return _close

    def _menu_item(self, label: str, checked: bool | None, on_click, close) -> ft.Container:
        """A single context-menu row. checked=None → no checkbox; True/False → checkbox."""
        controls: list[ft.Control] = []
        if checked is not None:
            controls.append(ft.Icon(
                ft.Icons.CHECK_BOX if checked else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                size=14,
                color=_TOGGLE_ON if checked else _TEXT_FAINT,
            ))
        controls.append(ft.Text(label, size=12, color=_TEXT_PRIMARY, no_wrap=True))

        def _click(_e):
            if callable(close):
                close()
            if callable(on_click):
                on_click()

        return ft.Container(
            content=ft.Row(controls, spacing=7, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=10, vertical=7),
            border_radius=5,
            on_click=_click,
            on_hover=lambda e: (
                setattr(e.control, "bgcolor", "#33343a" if e.data == "true" else ft.Colors.TRANSPARENT)
                or (e.control.update() if e.control.page else None)
            ),
        )

    def _open_context_menu(self, x: float, y: float, options: list[tuple]) -> None:
        """Open a compact context menu at (x, y). options: list of (label, checked|None, callback)."""
        # Fixed width sized to the longest label (a single short item stays compact).
        # CJK glyphs are ~2x the width of Latin, so measure per-character instead of
        # len()*constant — otherwise Chinese/Japanese/Korean labels get cut off.
        def _label_px(lbl: str) -> float:
            return sum(13.5 if ord(ch) > 0x2E7F else 6.6 for ch in lbl)

        longest_px = max((_label_px(lbl) for (lbl, *_rest) in options), default=66.0)
        has_check = any(opt[1] is not None for opt in options)
        has_trail = any(len(opt) > 3 and opt[3] is not None for opt in options)
        width = min(420.0, max(132.0, longest_px + (22.0 if has_check else 0.0)
                               + (28.0 if has_trail else 0.0) + 30.0))
        holder: dict = {}

        def _guard(fn):
            # Menu callbacks run inside flet's async machinery, which
            # swallows exceptions into never-retrieved futures — actions
            # then silently do nothing. Surface them in the app log.
            def _inner():
                try:
                    fn()
                except Exception:
                    import logging

                    logging.getLogger(__name__).exception(
                        "[OCR] context-menu action failed")
            return _inner

        def _close():
            holder.get("close", lambda: None)()

        rows = []
        for opt in options:
            lbl, checked, cb = opt[0], opt[1], opt[2]
            row = self._menu_item(lbl, checked, _guard(cb), _close)
            trail = opt[3] if len(opt) > 3 else None
            if trail is not None:
                # Trailing toggle icon on the same row (e.g. the region
                # lock beside "Set OCR region") — one line, two actions.
                t_on, t_cb = trail
                t_fn = _guard(t_cb)
                row.content.tight = False
                row.content.controls.append(ft.Container(expand=True))
                row.content.controls.append(ft.Container(
                    content=ft.Icon(
                        ft.Icons.LOCK if t_on else ft.Icons.LOCK_OPEN,
                        size=14,
                        color=_TOGGLE_ON if t_on else _TEXT_FAINT,
                    ),
                    padding=ft.padding.symmetric(horizontal=5, vertical=1),
                    border_radius=4,
                    on_click=lambda _e, f=t_fn: (_close(), f()),
                ))
            rows.append(row)
        close = self._open_popover_at(x, y, ft.Column(rows, spacing=1, tight=True), width=width)
        holder["close"] = close

    @staticmethod
    def _tap_xy(e) -> tuple[float, float]:
        return (
            float(getattr(e, "global_x", None) or 0.0),
            float(getattr(e, "global_y", None) or 0.0),
        )


    def _on_chat_scroll(self, e) -> None:
        """Track whether the user is at the bottom of the log. While they're scrolled
        up, stop following new messages and show the jump-to-latest button."""
        try:
            max_ext = float(getattr(e, "max_scroll_extent", 0) or 0)
            pixels = float(getattr(e, "pixels", 0) or 0)
        except Exception:
            return
        at_bottom = max_ext <= 0 or (max_ext - pixels) <= 48
        self._chat_following = at_bottom
        self._set_chat_jump_visible(not at_bottom)

    def _follow_chat_if_following(self) -> None:
        """Called after appending a message: scroll to the newest only if the user is
        still at the bottom; otherwise surface the jump button so they can catch up."""
        lv = self._chat_list_view
        if lv is None or not lv.page:
            return
        if self._chat_following:
            try:
                lv.scroll_to(offset=-1, duration=120)
            except Exception:
                pass
        else:
            self._set_chat_jump_visible(True)

    def _on_jump_to_latest(self, e=None) -> None:
        self._chat_following = True
        self._set_chat_jump_visible(False)
        lv = self._chat_list_view
        if lv is not None and lv.page:
            try:
                lv.scroll_to(offset=-1, duration=200)
            except Exception:
                pass

    def _set_chat_jump_visible(self, visible: bool) -> None:
        btn = getattr(self, "_chat_jump_btn", None)
        if btn is None or btn.visible == visible:
            return
        btn.visible = visible
        try:
            if btn.page:
                btn.update()
        except Exception:
            pass

    def _on_chat_filter_peer_click(self, e) -> None:
        self._filter_peer_lang_active = not self._filter_peer_lang_active
        self._refresh_filter_peer_btn()
        if callable(self.on_filter_peer_by_target_languages_change):
            self.on_filter_peer_by_target_languages_change(self._filter_peer_lang_active)

    def _refresh_filter_peer_btn(self) -> None:
        active = self._filter_peer_lang_active
        btn = self._filter_peer_btn
        btn.content.color = _RECV_COLOR if active else _TEXT_FAINT
        btn.bgcolor = "#2d1f33" if active else ft.Colors.TRANSPARENT
        btn.border = ft.border.all(1, _RECV_COLOR if active else "#3a3b3f")
        try:
            btn.update()
        except Exception:
            pass

    def set_filter_peer_by_target_languages(self, enabled: bool) -> None:
        self._filter_peer_lang_active = bool(enabled)
        self._refresh_filter_peer_btn()

    def _on_chat_clear(self, e) -> None:
        if self._chat_list_view is None:
            return
        self._chat_list_view.controls.clear()
        try:
            self._chat_list_view.update()
        except Exception:
            pass

    def append_chat_entry(
        self,
        *,
        channel: str,
        source: str,
        source_text: str,
        translated_text: str,
        src_lang_hint: str = "",
    ) -> None:
        if self._chat_list_view is None:
            return
        import datetime as _dt
        timestamp = _dt.datetime.now().strftime("%H:%M")
        is_peer = channel == "peer"
        is_ocr = channel == "ocr"
        if is_ocr:
            label_color = "#6ab7e8"  # light blue — OCR capture entries
            direction = t("dashboard.chat.received_ocr")
        else:
            label_color = _RECV_COLOR if is_peer else _SENT_COLOR
            direction = (t("dashboard.chat.received") if is_peer
                         else t("dashboard.chat.sent"))
        # Determine source/target language for transliteration. The hint is
        # the translator-DETECTED language (e.g. DeepL saw Japanese) — it
        # beats every configured guess.
        from puripuly_heart.core.transliteration import sniff_translit_language
        if is_ocr:
            # OCR can't know the speaker's language — sniff the SCRIPT of
            # the captured text. Guessing from configured languages sent
            # Chinese through the Japanese romaji engine ('你是在香港吗' →
            # '?? zaiHonkon ?': kakasi read 香港 as kanji, ?? = unmapped).
            src_lang = sniff_translit_language(source_text or "")
            tgt_lang = self._source_lang_code  # translated into user's lang
        elif is_peer:
            src_lang = src_lang_hint or self._peer_source_lang_code
            if not src_lang_hint and getattr(self, "_auto_detect_voice", False):
                # Auto detect voice: the configured Target language is only
                # an assumption — trust the text's script instead, or the
                # reading line vanishes when the speech is another language.
                src_lang = sniff_translit_language(source_text or "", src_lang)
            tgt_lang = self._effective_peer_target_lang_code()  # always has a value
        else:
            src_lang = src_lang_hint or self._source_lang_code
            tgt_lang = self._target_lang_code

        _TRANSLIT_COLOR = "#5ba8a0"
        content_rows: list[ft.Control] = []
        has_translation = bool(source_text and translated_text and source_text.strip() != translated_text.strip())

        # Log romanization follows the display toggle (show_*) only — the chatbox
        # The in-app log has its OWN format ("Chat log" in the options menu),
        # independent of what the chatbox sends to VRChat.
        _fmt = str(getattr(self, "_chat_log_format", "orig_read_trans"))
        if _fmt not in ("orig_trans", "orig_read_trans", "read_trans",
                        "read_only", "trans_only"):
            _fmt = "orig_read_trans"
        _inc_src = _fmt in ("orig_trans", "orig_read_trans")
        _read_only = _fmt == "read_only"
        _want_read = _fmt in ("orig_read_trans", "read_trans", "read_only")
        _want_romaji = _want_pinyin = _want_latin = _want_read
        if is_ocr:
            # OCR entries always romanize by the sniffed script — the
            # overlay shows pinyin for these lines, the log should match
            # even when the user's own display toggles differ.
            _want_pinyin = _want_romaji = True
        if has_translation:
            translit_src = transliterate_for_language(
                source_text, src_lang, show_pinyin=_want_pinyin, show_romaji=_want_romaji, show_latin=_want_latin
            )
            translit_tgt = transliterate_for_language(
                translated_text, tgt_lang, show_pinyin=_want_pinyin, show_romaji=_want_romaji, show_latin=_want_latin
            )
            # Source text with optional transliteration (if source is CJK)
            if translit_src and _inc_src:
                content_rows.append(ft.Text(translit_src, size=11, color=_TRANSLIT_COLOR, italic=True))
            if _inc_src:
                content_rows.append(ft.Text(source_text.strip(), size=12, color=_TEXT_FAINT))
            # Translation block: pinyin/romaji above, then translation text
            if translit_tgt and (translit_tgt != translit_src or not _inc_src):
                content_rows.append(ft.Text(translit_tgt, size=11, color=_TRANSLIT_COLOR, italic=True))
            if _read_only:
                # Reading only: keep just the translit lines; fall back to the
                # translation when nothing was romanizable at all.
                if not content_rows:
                    content_rows.append(ft.Text(translated_text.strip(), size=13, color=_TEXT_PRIMARY, weight=ft.FontWeight.W_500))
            else:
                content_rows.append(ft.Text(translated_text.strip(), size=13, color=_TEXT_PRIMARY, weight=ft.FontWeight.W_500))
        elif translated_text:
            translit = transliterate_for_language(
                translated_text, tgt_lang, show_pinyin=_want_pinyin, show_romaji=_want_romaji, show_latin=_want_latin
            )
            if translit:
                content_rows.append(ft.Text(translit, size=11, color=_TRANSLIT_COLOR, italic=True))
            content_rows.append(ft.Text(translated_text.strip(), size=13, color=_TEXT_PRIMARY, weight=ft.FontWeight.W_500))
        else:
            content_rows.append(ft.Text(source_text.strip(), size=13, color=_TEXT_PRIMARY))

        # Header: just "Sent 16:37" — clean timestamp label
        header = ft.Row(
            [
                ft.Text(direction, size=11, color=label_color, weight=ft.FontWeight.W_700),
                ft.Text(f" {timestamp}", size=11, color=_TEXT_FAINT),
            ],
            spacing=0,
            tight=True,
        )

        # If a pending sent entry exists and this is a self-channel result, update it in-place
        if not is_peer and not is_ocr and self._pending_sent_col is not None:
            self._pending_version += 1  # cancel timeout
            col = self._pending_sent_col
            self._pending_sent_col = None
            col.controls.clear()
            col.controls.extend([header, *content_rows])
            self._last_chat_content_col = col
            try:
                if self._chat_list_view.page:
                    self._chat_list_view.update()
                    self._follow_chat_if_following()
            except Exception:
                pass
            return

        entry = ft.Container(
            content=ft.Column(
                [header, *content_rows],
                spacing=1,
                tight=True,
            ),
            padding=ft.padding.only(left=10, top=6, bottom=6, right=8),
            # NOTE: no border_radius — a non-uniform border (left only) combined with a
            # border_radius makes Flutter flash the rounded-rect bounds of every entry
            # during scroll repaints (the faint boxes). Square entries avoid that.
            border=ft.border.only(left=ft.BorderSide(2, label_color)),
            margin=ft.margin.only(top=4),
        )
        self._last_chat_content_col = entry.content  # track for extra-language appends
        self._chat_list_view.controls.append(entry)
        if len(self._chat_list_view.controls) > CHAT_MAX_ENTRIES:
            del self._chat_list_view.controls[:20]
        try:
            self._chat_list_view.update()
            self._follow_chat_if_following()
        except Exception:
            pass

    def append_extra_chat_lines(self, extra_pairs: list[tuple[str, str]]) -> None:
        """Append extra-language translation lines to the most recent chat entry."""
        col = getattr(self, "_last_chat_content_col", None)
        if col is None or self._chat_list_view is None:
            return
        _TRANSLIT_COLOR = "#5ba8a0"
        # Log romanization follows the display toggle (show_*) only — the chatbox
        # "Output Format" (send_*) shapes the VRChat message, not the in-app log.
        _want_romaji = self.show_romaji
        _want_pinyin = self.show_pinyin
        _want_latin = self.show_latin
        for lang_code, text in extra_pairs:
            if not text.strip():
                continue
            translit = transliterate_for_language(
                text, lang_code, show_pinyin=_want_pinyin, show_romaji=_want_romaji, show_latin=_want_latin
            )
            if translit:
                col.controls.append(ft.Text(translit, size=11, color=_TRANSLIT_COLOR, italic=True))
            col.controls.append(ft.Text(text.strip(), size=13, color=_TEXT_PRIMARY, weight=ft.FontWeight.W_500))
        try:
            self._chat_list_view.update()
            self._follow_chat_if_following()
        except Exception:
            pass

    # ── Submit / input ───────────────────────────────────────────────────────

    def _on_submit(self, text: str):
        self.set_display_text(text, language_code=self._source_lang_code)
        if self._chat_list_view is not None and self._show_pending_echo:
            import datetime as _dt
            timestamp = _dt.datetime.now().strftime("%H:%M")
            pending_text = ft.Text(text.strip(), size=13, color=_TEXT_FAINT, italic=True)
            pending_col = ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(t("dashboard.chat.sent"), size=11, color=_SENT_COLOR, weight=ft.FontWeight.W_700),
                            ft.Text(f" {timestamp}", size=11, color=_TEXT_FAINT),
                        ],
                        spacing=0, tight=True,
                    ),
                    pending_text,
                ],
                spacing=1, tight=True,
            )
            entry = ft.Container(
                content=pending_col,
                padding=ft.padding.only(left=10, top=6, bottom=6, right=8),
                # No border_radius — see the note on the finalized entry above (avoids
                # the faint per-entry boxes flashing during scroll).
                border=ft.border.only(left=ft.BorderSide(2, _SENT_COLOR)),
                margin=ft.margin.only(top=4),
            )
            self._last_chat_content_col = pending_col
            self._chat_list_view.controls.append(entry)
            if len(self._chat_list_view.controls) > CHAT_MAX_ENTRIES:
                del self._chat_list_view.controls[:20]
            # The user just sent a message — always jump to the bottom and resume
            # following, even if they were scrolled up reading history.
            self._chat_following = True
            self._set_chat_jump_visible(False)
            try:
                self._chat_list_view.update()
                self._follow_chat_if_following()
            except Exception:
                pass
            self._pending_sent_col = pending_col
            self._pending_version += 1
            version = self._pending_version

            async def _timeout():
                import asyncio as _asyncio
                await _asyncio.sleep(6)
                if self._pending_version != version or self._pending_sent_col is not pending_col:
                    return
                self._pending_sent_col = None
                pending_col.controls.append(
                    ft.Text(
                        t("dashboard.chat.translation_failed"),
                        color="#e05050", size=11, italic=True,
                    )
                )
                pending_text.color = "#888888"
                try:
                    if self._chat_list_view and self._chat_list_view.page:
                        self._chat_list_view.update()
                except Exception:
                    pass

            if self.page:
                self.page.run_task(_timeout)
        if self.on_send_message:
            self.on_send_message("You", text)

    def _on_msg_input_submit(self, e) -> None:
        text = (e.control.value or "").strip()
        if text:
            e.control.value = ""
            try:
                e.control.update()
            except Exception:
                pass
            self._on_submit(text)

    def _on_send_btn_click(self, _e) -> None:
        if not hasattr(self, "_msg_input"):
            return
        text = (self._msg_input.value or "").strip()
        if text:
            self._msg_input.value = ""
            try:
                self._msg_input.update()
            except Exception:
                pass
            self._on_submit(text)

    def _set_message_input_focused(self, focused: bool) -> None:
        self._message_input_focused = bool(focused)

    def handle_message_input_tab_key(self) -> bool:
        if not self._message_input_focused:
            return False
        self._swap_languages()
        if hasattr(self, "_msg_input"):
            try:
                self._msg_input.focus()
            except Exception:
                pass
        return True

    def _on_swap_hover(self, e) -> None:
        pass  # hover effect removed with text label

    # ── Preset tab handlers ──────────────────────────────────────────────────

    def _on_preset_tab_click(self, index: int) -> None:
        if index == self._active_preset:
            return
        # Save current state back to preset data
        self._preset_data[self._active_preset] = {
            "source": self._source_lang_code,
            "targets": [self._target_lang_code] + list(self._extra_target_lang_codes),
            "peer_source": self._peer_source_lang_code,
            "peer_target": self._peer_target_lang_code,
        }
        # Load new preset
        self._active_preset = index
        preset = self._preset_data[index]
        self._source_lang_code = preset["source"]
        targets = preset.get("targets", ["en"])
        self._target_lang_code = targets[0] if targets else "en"
        self._extra_target_lang_codes = list(targets[1:])
        self._peer_source_lang_code = preset.get("peer_source", "")
        self._peer_target_lang_code = preset.get("peer_target", "")
        # Unified view: the (hidden) text target mirrors the partner's language.
        self._apply_unified_target_sync()
        self._update_input_font()
        self._refresh_language_panel()
        self._refresh_language_rows()
        self._notify_language_change()

    def _on_add_extra_target(self, _=None) -> None:
        if len(self._extra_target_lang_codes) >= self._MAX_EXTRA_LANGS:
            return
        self._extra_target_lang_codes.append("ja")
        self._rebuild_extra_tgt_rows()
        self._notify_language_change()

    def _on_remove_extra_target(self, idx: int) -> None:
        if 0 <= idx < len(self._extra_target_lang_codes):
            del self._extra_target_lang_codes[idx]
        self._rebuild_extra_tgt_rows()
        self._notify_language_change()

    def _on_add_extra_peer_target(self, _=None) -> None:
        if len(self._extra_peer_target_lang_codes) >= self._MAX_EXTRA_LANGS:
            return
        self._extra_peer_target_lang_codes.append("ja")
        self._rebuild_extra_peer_tgt_rows()
        self._notify_language_change()

    def _on_remove_extra_peer_target(self, idx: int) -> None:
        if 0 <= idx < len(self._extra_peer_target_lang_codes):
            del self._extra_peer_target_lang_codes[idx]
        self._rebuild_extra_peer_tgt_rows()
        self._notify_language_change()

    def _rebuild_extra_tgt_rows(self) -> None:
        """Rebuild the dynamic extra target language rows from _extra_target_lang_codes."""
        _BTN_SLOT = 22
        rows: list[ft.Control] = []
        translit_cols: list[ft.Column] = []
        for i, lang_code in enumerate(self._extra_target_lang_codes):
            lbl = ft.Text(language_name(lang_code), size=12, color=_TEXT_MUTED, no_wrap=True,
                          overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER, expand=True)
            arrow = ft.Icon(ft.Icons.CHEVRON_RIGHT, size=12, color=_TEXT_FAINT)
            card = ft.Container(
                content=ft.Row([lbl, arrow], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor="#2a2b2e", border_radius=6, border=ft.border.all(1, "#3a3b3e"),
                padding=ft.padding.symmetric(horizontal=8, vertical=5), expand=True,
                tooltip=self._lang_card_tooltip(lang_code, kind="target"),
                on_click=lambda _, idx=i: self._open_extra_target_dialog(idx),
                on_hover=lambda e, l=lbl: (
                    setattr(l, "color", _TOGGLE_ON if e.data == "true" else _TEXT_MUTED)
                    or (l.update() if l.page else None)
                ),
            )
            minus = ft.Container(
                content=ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
                on_click=lambda _, idx=i: self._on_remove_extra_target(idx),
                tooltip=t("dashboard.tooltip.remove_target"), width=_BTN_SLOT,
            )
            card_row = ft.Row([card, minus], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            translit_col = self._build_translit_col(lang_code)
            translit_cols.append(translit_col)
            rows.append(ft.Column([card_row, translit_col], spacing=3, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        self._extra_tgt_translit_cols = translit_cols
        self._extra_tgt_rows_col.controls = rows
        try:
            if self._extra_tgt_rows_col.page:
                self._extra_tgt_rows_col.update()
        except Exception:
            pass
        self._refresh_auto_translit()

    def _rebuild_extra_peer_tgt_rows(self) -> None:
        """Rebuild the dynamic extra peer target language rows from _extra_peer_target_lang_codes."""
        _BTN_SLOT = 22
        rows: list[ft.Control] = []
        translit_cols: list[ft.Column] = []
        for i, lang_code in enumerate(self._extra_peer_target_lang_codes):
            lbl = ft.Text(language_name(lang_code), size=12, color=_TEXT_MUTED, no_wrap=True,
                          overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER, expand=True)
            arrow = ft.Icon(ft.Icons.CHEVRON_RIGHT, size=12, color=_TEXT_FAINT)
            card = ft.Container(
                content=ft.Row([lbl, arrow], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor="#2a2b2e", border_radius=6, border=ft.border.all(1, "#3a3b3e"),
                padding=ft.padding.symmetric(horizontal=8, vertical=5), expand=True,
                tooltip=self._lang_card_tooltip(lang_code, kind="peer_target"),
                on_click=lambda _, idx=i: self._open_extra_peer_target_dialog(idx),
                on_hover=lambda e, l=lbl: (
                    setattr(l, "color", _TOGGLE_ON if e.data == "true" else _TEXT_MUTED)
                    or (l.update() if l.page else None)
                ),
            )
            minus = ft.Container(
                content=ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
                on_click=lambda _, idx=i: self._on_remove_extra_peer_target(idx),
                tooltip=t("dashboard.tooltip.remove_peer_target"), width=_BTN_SLOT,
            )
            card_row = ft.Row([card, minus], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            translit_col = self._build_translit_col(lang_code)
            translit_cols.append(translit_col)
            rows.append(ft.Column([card_row, translit_col], spacing=3, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        self._extra_peer_tgt_translit_cols = translit_cols
        self._extra_peer_tgt_rows_col.controls = rows
        try:
            if self._extra_peer_tgt_rows_col.page:
                self._extra_peer_tgt_rows_col.update()
        except Exception:
            pass

    def _on_add_alt_source(self, _=None) -> None:
        if self._alt_source_lang_code is not None:
            return
        self._alt_source_lang_code = "ko" if self._source_lang_code != "ko" else "ja"
        self._refresh_alt_source()
        self._notify_language_change()

    def _on_remove_alt_source(self, _=None) -> None:
        if self._alt_source_lang_code is None:
            return
        self._alt_source_lang_code = None
        self._refresh_alt_source()
        self._notify_language_change()

    def _refresh_alt_source(self) -> None:
        alt = self._alt_source_lang_code
        self._alt_src_lang_card.content.controls[0].value = language_name(alt) if alt else ""
        self._alt_src_lang_card.tooltip = language_name(alt) if alt else None
        self._alt_src_row.visible = alt is not None
        self._src_plus_btn.visible = alt is None
        for ctrl in (self._alt_src_row, self._src_plus_btn, self._alt_src_lang_card):
            try:
                ctrl.update()
            except Exception:
                pass

    def _open_alt_source_dialog(self, _=None):
        modal = LanguageModal(page=self.page, languages=self._LANG_OPTIONS, on_select=self._on_alt_source_select)
        modal.open(current=self._alt_source_lang_code or "ko", recent=self._recent_source_langs)

    def _on_alt_source_select(self, lang_code: str) -> None:
        # selecting alt source also activates it as the current source
        old = self._source_lang_code
        self._source_lang_code, self._alt_source_lang_code = lang_code, old
        self._add_to_recent(lang_code, is_source=True)
        self._update_input_font()
        self._refresh_language_panel()
        self._refresh_alt_source()
        self._notify_language_change()

    def _refresh_language_panel(self) -> None:
        # Update tab button appearances
        for i, tab in enumerate(self._preset_tab_containers):
            is_active = (i == self._active_preset)
            tab.bgcolor = _TOGGLE_ON if is_active else "#333537"
            txt = tab.content
            txt.color = "#ffffff" if is_active else _TEXT_FAINT
            txt.weight = ft.FontWeight.W_700 if is_active else ft.FontWeight.NORMAL
            try:
                tab.update()
            except Exception:
                pass
        # Update language card labels + tooltips
        src_name = language_name(self._source_lang_code)
        tgt1_name = language_name(self._target_lang_code)
        self._src_lang_card.content.controls[0].value = src_name
        self._src_lang_card.tooltip = self._lang_card_tooltip(self._source_lang_code, kind="self")
        self._tgt1_lang_card.content.controls[0].value = tgt1_name
        self._tgt1_lang_card.tooltip = self._lang_card_tooltip(self._target_lang_code, kind="target")
        self._refresh_translit_col(self._tgt1_translit_col, self._target_lang_code)
        self._rebuild_extra_tgt_rows()
        # Update mini sidebar language indicator
        try:
            src_short = self._source_lang_code.upper()[:2]
            tgt_short = self._target_lang_code.upper()[:2]
            self._mini_lang_text.value = f"{src_short}\n{tgt_short}"
            self._mini_lang_text.update()
        except Exception:
            pass
        for ctrl in (self._src_lang_card, self._tgt1_lang_card, self._tgt1_translit_col):
            try:
                ctrl.update()
            except Exception:
                pass

    # ── Language dialogs ─────────────────────────────────────────────────────

    # ── Translator selector ───────────────────────────────────────────────────

    def set_translator_label(self, label: str, model_value: str = "") -> None:
        self._translator_value_text.value = label
        if model_value:
            self._current_translator_model_value = model_value
        self._current_translator_label = label
        self._refresh_trans_tooltip()
        # Show the active translation model under the TRANS row, like MIC/PEER do.
        self._row_trans.set_sublabel(label)
        try:
            if self._translator_value_text.page:
                self._translator_value_text.update()
        except Exception:
            pass

    def set_translator_usage(self, usage_text: str | None) -> None:
        """Set the API-usage line shown in the TRANS tooltip (e.g. DeepL characters).

        Pass None to clear it (provider has no usage API, e.g. local Qwen).
        """
        self._translator_usage_text = usage_text or None
        self._refresh_trans_tooltip()

    def _refresh_trans_tooltip(self) -> None:
        label = getattr(self, "_current_translator_label", "") or "—"
        lines = [
            t("dashboard.trans.tooltip"),
            t("dashboard.tooltip.model", model=label),
        ]
        usage = getattr(self, "_translator_usage_text", None)
        if usage:
            lines.append(usage)
        lines.append(t("dashboard.tooltip.right_click_change"))
        self._row_trans.set_tooltip("\n".join(lines))

    def _on_translator_btn_click(self, _=None) -> None:
        if not self.page:
            return
        # Refresh provider usage (e.g. DeepL characters) so the TRANS tooltip is fresh.
        if callable(self.on_request_deepl_usage_refresh):
            try:
                self.on_request_deepl_usage_refresh()
            except Exception:
                pass
        from puripuly_heart.config.settings import TranslationModel

        _ORDERED_MODELS = (
            TranslationModel.GEMMA4,
            TranslationModel.DEEPSEEK_V4_FLASH,
            TranslationModel.DEEPSEEK_V4_PRO,
            TranslationModel.GEMINI_3_FLASH,
            TranslationModel.GEMINI_31_FLASH_LITE,
            TranslationModel.QWEN_35_PLUS,
            TranslationModel.DEEPL,
            TranslationModel.GOOGLE_TRANSLATE,
            TranslationModel.BING,
            TranslationModel.PAPAGO,
            TranslationModel.LOCAL_LLM,
        )
        _LABELS = {
            TranslationModel.GEMMA4: "Gemma 4 26B",
            TranslationModel.DEEPSEEK_V4_FLASH: "DeepSeek V4 Flash",
            TranslationModel.DEEPSEEK_V4_PRO: "DeepSeek V4 Pro",
            TranslationModel.GEMINI_3_FLASH: "Gemini 3 Flash",
            TranslationModel.GEMINI_31_FLASH_LITE: "Gemini 3.1 Flash-Lite",
            TranslationModel.QWEN_35_PLUS: "Qwen 3.5 Plus",
            TranslationModel.DEEPL: "DeepL",
            TranslationModel.GOOGLE_TRANSLATE: "Google Translate",
            TranslationModel.BING: "Bing",
            TranslationModel.PAPAGO: "Papago",
            TranslationModel.LOCAL_LLM: "Local LLMs",
        }
        # Models that require an API key (no managed/free fallback)
        _NEEDS_KEY = {
            TranslationModel.DEEPSEEK_V4_PRO,
            TranslationModel.GEMINI_3_FLASH,
            TranslationModel.GEMINI_31_FLASH_LITE,
            TranslationModel.QWEN_35_PLUS,
            TranslationModel.DEEPL,
        }
        # Dashboard pickers are GATED on a VERIFIED key (api_key_verified) —
        # key entry happens in the cog Settings, whose picker is deliberately
        # unrestricted (selecting a model there reveals its key field). The
        # dashboard is the quick-switch surface: only working options.
        options = []
        for m in _ORDERED_MODELS:
            needs_key = m in _NEEDS_KEY and not self._translator_model_has_key.get(m.value, False)
            desc = t("settings_modal.requires_api_key") if needs_key else ""
            options.append(OptionItem(value=m.value, label=_LABELS.get(m, m.value), description=desc, disabled=needs_key))
        # Usable options first; greyed-out ones sink below (stable sort keeps
        # each group's original order).
        options.sort(key=lambda o: o.disabled)
        # Prefer the live model from settings so the highlight is always accurate,
        # even if the cached label value drifted (e.g. a temporary fallback).
        current_val = ""
        if callable(self.on_request_current_translator):
            try:
                current_val = self.on_request_current_translator() or ""
            except Exception:
                current_val = ""
        if not current_val:
            current_val = getattr(self, "_current_translator_model_value", "") or ""
        SettingsModal(
            self.page,
            "Translator",
            options,
            self._on_translator_selected,
            show_description=True,
        ).open(current_val)

    def _on_translator_selected(self, value: str) -> None:
        if callable(self.on_translator_change):
            self.on_translator_change(value)

    def _on_trans_right_click(self, _=None) -> None:
        self._on_translator_btn_click()

    def set_whisper_availability(self, available: bool, reason_key: str = "") -> None:
        self._whisper_available = bool(available)
        self._whisper_unavailable_reason = reason_key or ""

    def _build_stt_options(self, for_language: str = "") -> list:
        """for_language: the channel's configured language ("" = auto-detect) so
        options that can't handle it are greyed out with a reason."""
        from puripuly_heart.config.settings import STTProviderName
        from puripuly_heart.ui.i18n import provider_label
        # LOCAL_QWEN and WHISPER are local models — always free, no API key needed
        _FREE_PROVIDERS = {STTProviderName.LOCAL_QWEN.value, STTProviderName.WHISPER.value}
        options = []
        for p in STTProviderName:
            if p.value in _FREE_PROVIDERS:
                needs_key = False
            else:
                needs_key = not self._stt_provider_has_key.get(p.value, False)
            disabled = needs_key
            desc = t("settings_modal.requires_api_key") if needs_key else ""
            # Whisper's model downloads from HuggingFace; only when that's unreachable (and
            # the model isn't cached) do we grey it out with a clear reason. When it's fine,
            # no description — don't nag about a download the user may already have.
            if p == STTProviderName.WHISPER and not self._whisper_available:
                disabled = True
                desc = t(self._whisper_unavailable_reason or "dashboard.whisper_hub_unreachable")
            # The local Qwen model can't hear every language. When this channel's
            # language is outside its set, selecting it would silently transcribe
            # nothing — grey it out with the reason instead of allowing a dead pick.
            if (
                p == STTProviderName.LOCAL_QWEN
                and for_language
                and not is_local_qwen_supported(for_language)
            ):
                disabled = True
                desc = t(
                    "stt.option.local_qwen_unsupported_language",
                    language=language_name(for_language),
                )
            options.append(OptionItem(value=p.value, label=provider_label(p.value), description=desc, disabled=disabled))
        # Usable options first; greyed-out ones sink below (stable sort keeps
        # each group's original order).
        options.sort(key=lambda o: o.disabled)
        return options

    def set_stt_key_flags(self, flags: dict) -> None:
        """Update which STT providers have their API key set. flags: {provider_value: bool}"""
        self._stt_provider_has_key.update(flags)

    def set_translator_key_flags(self, flags: dict) -> None:
        """Update which translation models have their API key set. flags: {model_value: bool}"""
        self._translator_model_has_key.update(flags)

    def _on_stt_right_click(self, _=None) -> None:
        if not self.page:
            return
        from puripuly_heart.config.settings import STTProviderName
        current = getattr(self, "_current_stt_provider_value", STTProviderName.LOCAL_QWEN.value)
        SettingsModal(self.page, "Mic (STT)", self._build_stt_options(for_language=self._source_lang_code), self._on_stt_provider_selected, show_description=True).open(current)

    def _on_stt_provider_selected(self, value: str) -> None:
        if callable(self.on_stt_provider_change):
            self.on_stt_provider_change(value)

    def _on_peer_right_click(self, _=None) -> None:
        if not self.page:
            return
        from puripuly_heart.config.settings import STTProviderName
        current = getattr(self, "_current_peer_stt_provider_value", STTProviderName.LOCAL_QWEN.value)
        SettingsModal(self.page, "Peer Voice (STT)", self._build_stt_options(for_language=self._peer_source_lang_code), self._on_peer_stt_provider_selected, show_description=True).open(current)

    def _on_peer_stt_provider_selected(self, value: str) -> None:
        if callable(self.on_peer_stt_provider_change):
            self.on_peer_stt_provider_change(value)

    def set_stt_provider_label(self, label: str, provider_value: str = "") -> None:
        if provider_value:
            self._current_stt_provider_value = provider_value
        self._refresh_stt_tooltip(label)
        self._row_stt.set_sublabel(label)

    def set_stt_input_device(self, device_name: str) -> None:
        self._stt_input_device = device_name or ""
        self._refresh_stt_tooltip()

    def _refresh_stt_tooltip(self, label: str | None = None) -> None:
        if label is None:
            label = getattr(self, "_current_stt_label", "")
        else:
            self._current_stt_label = label
        tip = t("dashboard.mic.tooltip") + "\n" + t("dashboard.tooltip.model", model=label)
        if self._stt_input_device:
            tip += "\n" + t("dashboard.tooltip.device", device=self._stt_input_device)
        tip += "\n" + t("dashboard.tooltip.right_click_change")
        self._row_stt.set_tooltip(tip)

    def set_peer_stt_provider_label(self, label: str, provider_value: str = "") -> None:
        if provider_value:
            self._current_peer_stt_provider_value = provider_value
        self._refresh_peer_tooltip(label)
        self._row_peer.set_sublabel(label)

    def _refresh_peer_tooltip(self, label: str | None = None) -> None:
        if label is None:
            label = getattr(self, "_current_peer_stt_label", "")
        else:
            self._current_peer_stt_label = label
        self._row_peer.set_tooltip(
            t("dashboard.peer.tooltip")
            + "\n" + t("dashboard.tooltip.model", model=label)
            + "\n" + t("dashboard.tooltip.right_click_change")
        )

    def _open_source_dialog(self, _=None):
        auto_label = t("language.auto", default="Auto Detect")
        source_langs = [("", auto_label)] + list(self._LANG_OPTIONS)
        modal = LanguageModal(page=self.page, languages=source_langs, on_select=self._on_source_select)
        modal.open(current=self._source_lang_code, recent=self._recent_source_langs)

    def _open_target_dialog(self, _=None):
        modal = LanguageModal(
            page=self.page,
            languages=self._LANG_OPTIONS,
            on_select=self._on_target_select,
        )
        modal.open(current=self._target_lang_code, recent=self._recent_target_langs)

    def _open_extra_target_dialog(self, idx: int = 0, _e=None):
        if idx >= len(self._extra_target_lang_codes):
            return
        modal = LanguageModal(
            page=self.page,
            languages=self._LANG_OPTIONS,
            on_select=lambda code, i=idx: self._on_extra_target_select(i, code),
        )
        modal.open(current=self._extra_target_lang_codes[idx], recent=self._recent_target_langs)

    # ── Inline transliteration chips ─────────────────────────────────────────

    _PINYIN_LANGS = {"zh", "cmn"}
    _ROMAJI_LANGS = {"ja", "jpn"}
    _ROMAJA_LANGS = {"ko", "kor"}
    _LATIN_LANGS = {"ru", "uk", "bg", "el", "ar", "hi", "th"}

    def _translit_script(self, lang_code: str) -> str | None:
        # Auto Detect (empty) → a generic "Latin" toggle that flips pinyin+romaji+latin
        # together, so whatever language the recognizer detects gets romanized.
        if not lang_code or not lang_code.strip():
            return "auto"
        base = lang_code.lower().split("-")[0]
        if base in self._PINYIN_LANGS:
            return "pinyin"
        if base in self._ROMAJI_LANGS:
            return "romaji"
        if base in self._ROMAJA_LANGS:
            return "romaja"
        if base in self._LATIN_LANGS:
            return "latin"
        return None

    # fmt id -> (include_source, send_reading, reading_only)
    _CHATBOX_FMT_FLAGS = {
        "orig_trans":      (True,  False, False),
        "orig_read_trans": (True,  True,  False),
        "read_trans":      (False, True,  False),
        "read_only":       (False, True,  True),
        "trans_only":      (False, False, False),
    }

    def _build_lang_swap_btn(self) -> ft.Control:
        """Header button that swaps Your language <-> Target language."""
        icon = ft.Container(
            content=ft.Icon(ft.Icons.SWAP_VERT, size=15, color=_TEXT_MUTED),
            padding=ft.padding.all(3), border_radius=4,
            tooltip=t("dashboard.tooltip.swap_languages"),
            on_click=self._on_swap_languages,
            on_hover=lambda e: (
                setattr(e.control, "bgcolor", "#33343a" if e.data == "true" else ft.Colors.TRANSPARENT)
                or (e.control.update() if e.control.page else None)
            ),
        )
        return icon

    def _on_swap_languages(self, _e=None) -> None:
        logger.info("[LangNotify] SWAP BUTTON clicked (event=%r)", _e)
        src, tgt = self._source_lang_code, self._peer_source_lang_code
        if not src or not tgt:
            # Auto Detect / legacy-empty can't move into the concrete
            # Target language slot — nothing sensible to swap.
            return
        self._source_lang_code, self._peer_source_lang_code = tgt, src
        # Extras swap too: the second "Your language" (alt) and the extra
        # Target language are both single optional slots, so they trade
        # places — swapping twice restores the original setup. An Auto
        # Detect ("") extra peer slot can't become an alt source; drop it.
        old_alt = self._alt_source_lang_code
        old_extras = list(self._extra_peer_source_lang_codes)
        new_alt = old_extras[0] if old_extras and old_extras[0] else None
        self._alt_source_lang_code = new_alt
        self._extra_peer_source_lang_codes = [old_alt] if old_alt else []
        # Re-assert the typed-target mirror against the new Target language.
        self._apply_unified_target_sync()
        self._update_input_font()
        self._refresh_language_panel()
        self._refresh_language_rows()
        self._refresh_alt_source()
        self._rebuild_extra_peer_src_rows()
        self._notify_language_change()

    def _build_translit_gear(self) -> ft.Control:
        """A single cog on the TEXT TRANSLATION header that opens the format menu."""
        icon = ft.Container(
            content=ft.Icon(ft.Icons.TUNE, size=14, color=_TEXT_MUTED),
            padding=ft.padding.all(3), border_radius=4,
            tooltip=t("dashboard.translit.options.tooltip"),
            on_hover=lambda e: (
                setattr(e.control, "bgcolor", "#33343a" if e.data == "true" else ft.Colors.TRANSPARENT)
                or (e.control.update() if e.control.page else None)
            ),
        )
        self._translit_gear = ft.GestureDetector(content=icon, on_tap_down=self._open_translit_menu)
        return self._translit_gear

    def _build_translit_col(self, lang_code: str) -> ft.Column:
        # Per-row transliteration chips were replaced by a single cog on the TEXT
        # TRANSLATION header. Keep an invisible placeholder so the row-building code that
        # references these columns keeps working without per-language controls.
        return ft.Column([], visible=False, height=0, spacing=0, data={"script": None})

    def _active_reading_script(self) -> str | None:
        """The script (pinyin/romaji/romaja/latin) of the first romanizable language in
        play — target, extra targets, or peer — so the cog menu shows the right reading
        word. Returns None when nothing in play is romanizable."""
        codes = [self._target_lang_code] + list(self._extra_target_lang_codes)
        with contextlib.suppress(Exception):
            codes.append(self._effective_peer_source_lang_code())
            codes.append(self._effective_peer_target_lang_code())
        for code in codes:
            sc = self._translit_script(code)
            if sc in ("pinyin", "romaji", "romaja", "latin"):
                return sc
        with contextlib.suppress(Exception):
            if self._auto_translit_should_show():
                return "auto"
        return None

    def _reading_word(self, script: str | None) -> str:
        return {
            "pinyin": t("dashboard.translit.pinyin"),
            "romaji": t("dashboard.translit.romaji"),
            "romaja": t("dashboard.translit.romaja"),
        }.get(script or "", t("dashboard.translit.romanization"))

    def _current_chatbox_fmt(self) -> str:
        if self._chatbox_reading_only:
            return "read_only"
        send_reading = self.send_pinyin or self.send_romaji or self.send_latin
        if self._chatbox_include_source and send_reading:
            return "orig_read_trans"
        if self._chatbox_include_source:
            return "orig_trans"
        if send_reading:
            return "read_trans"
        return "trans_only"

    def _pick_chatbox_fmt(self, fmt: str) -> None:
        inc, read, ronly = self._CHATBOX_FMT_FLAGS.get(fmt, (True, False, False))
        self._chatbox_include_source = inc
        self.send_pinyin = self.send_romaji = self.send_latin = read
        self._chatbox_reading_only = ronly
        if callable(self.on_chatbox_format_change):
            self.on_chatbox_format_change(fmt)

    def _pick_chat_log_fmt(self, fmt: str) -> None:
        self._chat_log_format = fmt
        if callable(self.on_chat_log_format_change):
            self.on_chat_log_format_change(fmt)

    def _toggle_overlay_reading(self) -> None:
        new = not (self.show_pinyin or self.show_romaji or self.show_latin)
        self.show_pinyin = self.show_romaji = self.show_latin = new
        self._emit_transliteration_change()

    def _open_translit_menu(self, e) -> None:
        """Rich popover matching the overlay right-click menu: a section header, a Chatbox
        'format' button that expands to a radio list (like Display/Size), and On/Off pills
        for the overlay reading + grouped pinyin."""
        x, y = self._tap_xy(e)
        script = self._active_reading_script()
        rw = self._reading_word(script)

        # ── shared styling helpers (mirror _on_overlay_right_click) ──────────────
        def _section_row(label: str, control: Any, top: int = 4,
                         tooltip: str | None = None) -> ft.Container:
            _row: list[Any] = [ft.Text(label, size=11, color=_TEXT_MUTED)]
            if tooltip:
                _row.append(ft.Container(
                    content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11, color=_TEXT_FAINT),
                    tooltip=tooltip,
                    padding=ft.padding.only(left=3),
                ))
            _row.append(ft.Container(expand=True))
            _row.append(control)
            return ft.Container(
                content=ft.Row(
                    _row, spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.padding.only(left=10, right=10, top=top, bottom=2),
            )


        def _row_tt(key: str, control, top: int = 4) -> ft.Container:
            # Labeled row with an auto info tooltip from '<key>.tooltip'.
            return _section_row(t(key), control, top=top,
                                tooltip=t(key + ".tooltip"))

        def _bool_pill(state_ref: list[bool], on_change) -> ft.Container:
            lbl = ft.Text(
                t("settings.option.on") if state_ref[0] else t("settings.option.off"),
                size=11, color=_TOGGLE_ON if state_ref[0] else _TEXT_FAINT,
                weight=ft.FontWeight.W_600,
            )
            box = ft.Container(
                content=lbl, padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=6, border=ft.border.all(1, _TOGGLE_ON if state_ref[0] else "#3a3b3f"),
            )
            def _click(_ev, _r=state_ref, _l=lbl, _b=box):
                _r[0] = not _r[0]
                _l.value = t("settings.option.on") if _r[0] else t("settings.option.off")
                _l.color = _TOGGLE_ON if _r[0] else _TEXT_FAINT
                _b.border = ft.border.all(1, _TOGGLE_ON if _r[0] else "#3a3b3f")
                try: _l.update(); _b.update()
                except Exception: pass
                on_change(_r[0])
            box.on_click = _click
            return box

        def _div() -> ft.Container:
            return ft.Container(height=1, bgcolor="#3a3b3f",
                                margin=ft.margin.symmetric(vertical=3, horizontal=6))

        # ── chatbox format: summary button that expands to a radio list ──────────
        fmt_ids = ["orig_trans"]
        if script is not None:
            fmt_ids += ["orig_read_trans", "read_trans", "read_only"]
        fmt_ids.append("trans_only")

        def _fmt_label(fid: str) -> str:
            return t(f"dashboard.translit.fmt.{fid}", system=rw)

        def _mk_fmt_selector(current_id: str, on_pick,
                             label_key: str) -> ft.Column:
            # Summary button expanding to a radio list; used twice — once for
            # the CHATBOX (what VRChat receives) and once for the CHAT LOG
            # (what the in-app log shows). Independent selections.
            cur = [current_id]
            _btn_text = ft.Text(
                _fmt_label(cur[0]), size=11, color=_TOGGLE_ON, weight=ft.FontWeight.W_600,
                no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
            )
            _btn = ft.Container(
                content=_btn_text, padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=6, bgcolor="#1a2e2a", border=ft.border.all(1, _TOGGLE_ON),
            )
            _icons: dict[str, Any] = {}
            _rows: list[Any] = []
            for fid in fmt_ids:
                _active = fid == cur[0]
                _ic = ft.Icon(
                    ft.Icons.RADIO_BUTTON_CHECKED if _active else ft.Icons.RADIO_BUTTON_UNCHECKED,
                    size=15, color=_TOGGLE_ON if _active else _TEXT_FAINT,
                )
                _icons[fid] = _ic
                _row_lbl = ft.Text(_fmt_label(fid), size=12, color=_TEXT_PRIMARY, expand=True)
                def _on_row(_ev, _f=fid):
                    if cur[0] == _f:
                        return
                    cur[0] = _f
                    on_pick(_f)
                    for _k, _i in _icons.items():
                        _sel = _k == _f
                        _i.name = ft.Icons.RADIO_BUTTON_CHECKED if _sel else ft.Icons.RADIO_BUTTON_UNCHECKED
                        _i.color = _TOGGLE_ON if _sel else _TEXT_FAINT
                    _btn_text.value = _fmt_label(_f)
                    try:
                        _btn_text.update()
                        for _i in _icons.values():
                            _i.update()
                    except Exception: pass
                _rows.append(ft.Container(
                    content=ft.Row([_ic, _row_lbl], spacing=8,
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.padding.only(left=18, right=10, top=6, bottom=6),
                    border_radius=5, on_click=_on_row,
                    on_hover=lambda e2: (
                        setattr(e2.control, "bgcolor", "#2a3040" if e2.data == "true" else ft.Colors.TRANSPARENT)
                        or (e2.control.update() if e2.control.page else None)
                    ),
                ))
            _rows_col = ft.Column(_rows, spacing=0, tight=True, visible=False)
            _expanded = [False]
            def _toggle(_ev):
                _expanded[0] = not _expanded[0]
                _rows_col.visible = _expanded[0]
                try: _rows_col.update()
                except Exception: pass
            _btn.on_click = _toggle
            return ft.Column(
                [_section_row(t(label_key), _btn,
                              tooltip=t(label_key + ".tooltip")),
                 _rows_col],
                spacing=0, tight=True,
            )

        _fmt_section = _mk_fmt_selector(
            self._current_chatbox_fmt(), self._pick_chatbox_fmt,
            "dashboard.translit.menu.chatbox_short")
        _log_fmt_cur = str(self._chat_log_format)
        if _log_fmt_cur not in fmt_ids:
            _log_fmt_cur = "trans_only" if _log_fmt_cur not in (
                "orig_trans", "orig_read_trans", "read_trans", "read_only",
                "trans_only") else _log_fmt_cur
        _log_fmt_section = _mk_fmt_selector(
            _log_fmt_cur if _log_fmt_cur in fmt_ids else "orig_trans",
            self._pick_chat_log_fmt,
            "dashboard.translit.menu.log_short")

        # ── overlay reading + grouped pinyin (On/Off pills), reading langs only ──
        extra_rows: list[Any] = []
        if script is not None:
            _sr_ref = [self.show_pinyin or self.show_romaji or self.show_latin]
            def _on_show(val: bool):
                self.show_pinyin = self.show_romaji = self.show_latin = val
                self._emit_transliteration_change()
            extra_rows.append(_section_row(
                t("dashboard.translit.menu.show_reading", system=rw), _bool_pill(_sr_ref, _on_show),
                tooltip=t("dashboard.translit.menu.show_reading.tooltip", system=rw)))
            # Grouped vs per-syllable(pinyin)/per-mora(romaji). Romaji is grouped by default;
            # turning this off gives the per-character reading.
            if script in ("pinyin", "romaji"):
                _gp_ref = [self._pinyin_word_grouping]
                extra_rows.append(_section_row(
                    t("dashboard.translit.words", system=rw),
                    _bool_pill(_gp_ref, self._on_pinyin_grouping_toggle),
                    tooltip=t("dashboard.translit.words.tooltip", system=rw)))

        # ── auto detect voice: peer voice language auto-detection, decoupled
        # from the concrete "Target language" pick (which typed messages use).
        _adv_ref = [bool(self._auto_detect_voice)]

        def _on_adv(val: bool):
            self._auto_detect_voice = bool(val)
            if callable(self.on_auto_detect_voice_change):
                self.on_auto_detect_voice_change(bool(val))

        _adv_row = _section_row(
            t("dashboard.menu.auto_detect_voice"), _bool_pill(_adv_ref, _on_adv),
            tooltip=t("dashboard.menu.auto_detect_voice.tooltip"))

        # ── separate text translation: mirrors the Settings row, applies live.
        _sep_ref = [not bool(self._unified_translation)]

        def _on_sep(val: bool):
            if callable(self.on_separate_text_translation_change):
                self.on_separate_text_translation_change(bool(val))

        _sep_row = _section_row(
            t("settings.separate_text_translation"), _bool_pill(_sep_ref, _on_sep),
            tooltip=t("settings.separate_text_translation.tooltip"))

        # ── assemble ─────────────────────────────────────────────────────────────
        children: list[Any] = [
            ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.TUNE, size=13, color=_TEXT_MUTED),
                    ft.Text(t("dashboard.translit.menu.title"), size=12,
                            color=_TEXT_MUTED, weight=ft.FontWeight.W_600),
                ], spacing=6, tight=True),
                padding=ft.padding.only(left=10, right=10, top=8, bottom=2),
            ),
            _fmt_section,
            _log_fmt_section,
        ]
        if extra_rows:
            children.append(_div())
            children += extra_rows
        children.append(_div())
        children.append(_adv_row)
        children.append(_sep_row)
        children.append(ft.Container(height=4))
        content = ft.Container(content=ft.Column(
            children, spacing=0, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        self._translit_popover_close = self._open_popover_at(
            x, y, content, width=280.0, est_height=500.0)

    def set_chatbox_format_state(self, include_source: bool, reading_only: bool) -> None:
        self._chatbox_include_source = bool(include_source)
        self._chatbox_reading_only = bool(reading_only)

    def _on_pinyin_grouping_toggle(self, value: bool) -> None:
        self._pinyin_word_grouping = value
        if callable(self.on_pinyin_word_grouping_change):
            self.on_pinyin_word_grouping_change(value)
        self._sync_translit_cols()

    def set_pinyin_word_grouping_state(self, value: bool) -> None:
        self._pinyin_word_grouping = bool(value)
        try:
            self._sync_translit_cols()
        except Exception:
            pass

    def _on_inline_chip_click(self, e, callback, icon: ft.Icon, lbl: ft.Text) -> None:
        chip = e.control
        # Determine new state by checking current icon
        is_now_on = icon.name == ft.Icons.CHECK_BOX_OUTLINE_BLANK
        icon.name = ft.Icons.CHECK_BOX if is_now_on else ft.Icons.CHECK_BOX_OUTLINE_BLANK
        icon.color = _TOGGLE_ON if is_now_on else _TEXT_FAINT
        lbl.color = _TEXT_MUTED if is_now_on else _TEXT_FAINT
        chip.bgcolor = "#2a2b2e"
        chip.border = ft.border.all(1, _TOGGLE_ON if is_now_on else "#3a3b3e")
        chip.update()
        callback(is_now_on)

    def _translit_label(self, script: str | None) -> str:
        key = {
            "pinyin": "dashboard.translit.pinyin",
            "romaji": "dashboard.translit.romaji",
            "romaja": "dashboard.translit.romaja",
        }.get(script or "", "dashboard.translit.latin")  # auto/None → Latin
        return t(key)

    def _translit_vals(self, script: str | None) -> tuple[bool, bool]:
        if script == "pinyin":
            return self.show_pinyin, self.send_pinyin
        if script in ("romaji", "romaja"):
            return self.show_romaji, self.send_romaji
        if script == "auto":
            return (
                self.show_pinyin or self.show_romaji or self.show_latin,
                self.send_pinyin or self.send_romaji or self.send_latin,
            )
        return self.show_latin, self.send_latin

    def _translit_show_cb(self, script: str | None):
        if script == "pinyin":
            return self._on_show_pinyin_toggle
        if script in ("romaji", "romaja"):
            return self._on_show_romaji_toggle
        if script == "auto":
            return self._on_show_romanization_toggle
        return self._on_show_latin_toggle

    def _translit_send_cb(self, script: str | None):
        if script == "pinyin":
            return self._on_send_pinyin_toggle
        if script in ("romaji", "romaja"):
            return self._on_send_romaji_toggle
        if script == "auto":
            return self._on_send_romanization_toggle
        return self._on_send_latin_toggle

    def _refresh_translit_col(self, col: ft.Column, lang_code: str) -> None:
        # Per-row transliteration controls now live on the header cog; keep the placeholder
        # hidden. The cog reads live state (incl. the active reading word) when opened.
        col.visible = False

    def _emit_transliteration_change(self) -> None:
        if callable(self.on_transliteration_change):
            self.on_transliteration_change(
                self.show_pinyin, self.send_pinyin,
                self.show_romaji, self.send_romaji,
                self.show_latin, self.send_latin,
            )

    def _on_show_pinyin_toggle(self, value: bool) -> None:
        self.show_pinyin = value
        self._emit_transliteration_change()
        self._sync_translit_cols()

    def _on_send_pinyin_toggle(self, value: bool) -> None:
        self.send_pinyin = value
        self._emit_transliteration_change()
        self._sync_translit_cols()

    def _on_show_romaji_toggle(self, value: bool) -> None:
        self.show_romaji = value
        self._emit_transliteration_change()
        self._sync_translit_cols()

    def _on_send_romaji_toggle(self, value: bool) -> None:
        self.send_romaji = value
        self._emit_transliteration_change()
        self._sync_translit_cols()

    def _on_show_latin_toggle(self, value: bool) -> None:
        self.show_latin = value
        self._emit_transliteration_change()
        self._sync_translit_cols()

    def _on_send_latin_toggle(self, value: bool) -> None:
        self.send_latin = value
        self._emit_transliteration_change()
        self._sync_translit_cols()

    def _on_show_romanization_toggle(self, value: bool) -> None:
        # Auto Detect: flip every "show" romanization flag so any detected language
        # (Korean→romaja, Chinese→pinyin, Arabic→latin, …) gets romanized.
        self.show_pinyin = value
        self.show_romaji = value
        self.show_latin = value
        self._emit_transliteration_change()
        self._sync_translit_cols()

    def _on_send_romanization_toggle(self, value: bool) -> None:
        self.send_pinyin = value
        self.send_romaji = value
        self.send_latin = value
        self._emit_transliteration_change()
        self._sync_translit_cols()

    def set_transliteration_flags(
        self,
        show_pinyin: bool,
        send_pinyin: bool,
        show_romaji: bool,
        send_romaji: bool,
        show_latin: bool = False,
        send_latin: bool = False,
    ) -> None:
        self.show_pinyin = show_pinyin
        self.send_pinyin = send_pinyin
        self.show_romaji = show_romaji
        self.send_romaji = send_romaji
        self.show_latin = show_latin
        self.send_latin = send_latin
        self._sync_translit_cols()

    def _auto_translit_should_show(self) -> bool:
        # The generic "Latin" toggle is a fallback for when a spoken/peer language is
        # Auto Detect AND no target already shows a per-language romanization chip.
        # If a target chip exists (e.g. Chinese → "Show Pinyin"), use that instead of
        # showing a second, redundant set.
        auto_present = (not self._source_lang_code) or (not self._peer_source_lang_code)
        if not auto_present:
            return False
        target_scripts = [self._translit_script(self._target_lang_code)] + [
            self._translit_script(c) for c in self._extra_target_lang_codes
        ]
        if any(s is not None for s in target_scripts):
            return False
        return True

    def _refresh_auto_translit(self) -> None:
        col = getattr(self, "_auto_translit_col", None)
        if col is None:
            return
        # Refresh the "auto" chip states, then override visibility (the generic toggle
        # only appears when a spoken/peer language is Auto Detect).
        self._refresh_translit_col(col, "")
        col.visible = False  # auto romanization now lives in the header cog menu
        try:
            if col.page:
                col.update()
        except Exception:
            pass

    def _sync_translit_cols(self) -> None:
        for col, lang in (
            [(self._tgt1_translit_col, self._target_lang_code)]
            + list(zip(self._extra_tgt_translit_cols, self._extra_target_lang_codes))
        ):
            try:
                self._refresh_translit_col(col, lang)
                if col.page:
                    col.update()
            except Exception:
                pass
        self._refresh_auto_translit()

    def _open_peer_source_dialog(self, _=None):
        # No Auto Detect entry here: the "Target language" must be concrete —
        # it drives the typed-message target in the unified view. Voice
        # auto-detection is its own toggle in the options (gear) menu.
        modal = LanguageModal(page=self.page, languages=self._LANG_OPTIONS, on_select=self._on_peer_source_select)
        modal.open(current=self._peer_source_lang_code, recent=self._recent_source_langs)

    def _open_peer_target_dialog(self, _=None):
        modal = LanguageModal(page=self.page, languages=self._LANG_OPTIONS, on_select=self._on_peer_target_select)
        modal.open(current=self._effective_peer_target_lang_code(), recent=self._recent_target_langs)

    def _open_extra_peer_target_dialog(self, idx: int = 0, _e=None):
        if idx >= len(self._extra_peer_target_lang_codes):
            return
        modal = LanguageModal(page=self.page, languages=self._LANG_OPTIONS,
                              on_select=lambda code, i=idx: self._on_extra_peer_target_select(i, code))
        modal.open(current=self._extra_peer_target_lang_codes[idx], recent=self._recent_target_langs)

    # ── Language selection callbacks ─────────────────────────────────────────

    def _apply_unified_target_sync(self) -> None:
        """Unified view: typed messages go out in the PARTNER's language, so
        the (hidden) text target mirrors the Voice 'Translate from' (peer
        speaks). Mirroring the 'Translate to' (reading) language instead would
        make target==source — a no-op that sends typed text untranslated.
        When 'Translate from' is Auto Detect there is no concrete language to
        mirror; the Text Translation card is revealed so the user defines the
        typed-output language themselves."""
        if getattr(self, "_unified_translation", True) and self._peer_source_lang_code:
            self._target_lang_code = self._peer_source_lang_code
        self._refresh_unified_text_card()

    def _refresh_unified_text_card(self) -> None:
        """Show the Text Translation card when it is meaningful: always in the
        separate layout, and in the unified layout only while the partner's
        language is Auto Detect (typed output needs a manual pick)."""
        card = getattr(self, "_text_section_card", None)
        if card is None:
            return
        visible = (not getattr(self, "_unified_translation", True)
                   or not self._peer_source_lang_code)
        if card.visible != visible:
            card.visible = visible
            try:
                if card.page:
                    card.update()
            except Exception:
                pass

    def set_unified_translation(self, enabled: bool) -> None:
        """Live-apply the Settings 'Separate text translation' toggle: retitle
        the voice card, reorder/show the Text Translation card, and re-assert
        the typed-target mirror."""
        enabled = bool(enabled)
        if enabled == self._unified_translation:
            return
        if enabled:
            # Going unified: the mirror is about to overwrite the target —
            # remember the separate-mode pick first.
            if self._target_lang_code:
                self._separate_target_pref = self._target_lang_code
        elif self._separate_target_pref:
            # Going separate: restore the remembered pick (e.g. Japanese)
            # instead of leaving the mirrored Target language in place.
            self._target_lang_code = self._separate_target_pref
        self._unified_translation = enabled
        key = ("dashboard.section.translation" if enabled
               else "dashboard.section.voice_translation")
        lbl = getattr(self, "_voice_section_lbl", None)
        if lbl is not None:
            lbl.value = t(key)
            # keep apply_locale() re-translating the right key later
            for i, (l, _k) in enumerate(self._section_header_labels):
                if l is lbl:
                    self._section_header_labels[i] = (l, key)
                    break
        # Unified: primary Translation card on top; separate: classic order.
        try:
            if enabled:
                order = [self._voice_section_card, self._text_section_card]
            else:
                order = [self._text_section_card, self._voice_section_card]
            self._middle_section.controls = [self._preset_row_container, *order]
        except Exception:
            pass
        self._apply_unified_target_sync()
        self._refresh_language_panel()
        self._refresh_language_rows()
        try:
            if self._middle_section.page:
                self._middle_section.update()
        except Exception:
            pass
        # Persist the re-mirrored typed target (no-op when nothing changed).
        self._notify_language_change()

    def _on_source_select(self, lang_code: str):
        self._source_lang_code = lang_code
        if lang_code:  # don't add "Auto" to recent
            self._add_to_recent(lang_code, is_source=True)
        # Unified view: re-assert the typed-target mirror.
        self._apply_unified_target_sync()
        self._update_input_font()
        self._refresh_language_panel()
        self._refresh_language_rows()
        self._notify_language_change()

    def _on_target_select(self, lang_code: str):
        self._target_lang_code = lang_code
        self._add_to_recent(lang_code, is_source=False)
        if not self._unified_translation and lang_code:
            # Remember the separate-mode pick so toggling unified (which
            # mirrors target := Target language) doesn't lose it.
            self._separate_target_pref = lang_code
            if callable(self.on_separate_target_pref_change):
                self.on_separate_target_pref_change(lang_code)
        self._refresh_language_panel()
        self._refresh_language_rows()
        self._notify_language_change()

    def _on_extra_target_select(self, idx: int, lang_code: str):
        if 0 <= idx < len(self._extra_target_lang_codes):
            self._extra_target_lang_codes[idx] = lang_code
        self._add_to_recent(lang_code, is_source=False)
        self._rebuild_extra_tgt_rows()
        self._notify_language_change()

    def _on_peer_source_select(self, lang_code: str):
        self._peer_source_lang_code = lang_code
        if lang_code:  # don't add Auto Detect ("") to recents
            self._add_to_recent(lang_code, is_source=True)
        # Unified view: typed messages follow the partner's language.
        self._apply_unified_target_sync()
        self._refresh_language_panel()
        self._refresh_language_rows()
        self._notify_language_change()

    def _on_peer_target_select(self, lang_code: str):
        # Empty = "follow my language": the peer voice translates into the source
        # ("You Speak") language. Picking your own source language collapses back to
        # that default rather than pinning an explicit (redundant) override.
        self._peer_target_lang_code = "" if lang_code == self._source_lang_code else lang_code
        self._add_to_recent(lang_code, is_source=False)
        self._refresh_language_rows()
        self._notify_language_change()

    def _on_extra_peer_target_select(self, idx: int, lang_code: str):
        if 0 <= idx < len(self._extra_peer_target_lang_codes):
            self._extra_peer_target_lang_codes[idx] = lang_code
        self._add_to_recent(lang_code, is_source=False)
        self._rebuild_extra_peer_tgt_rows()
        self._notify_language_change()

    # ── Extra peer source language (multi-listen) ────────────────────────────

    _MAX_EXTRA_LANGS = 1

    def _on_add_extra_peer_source(self, _=None) -> None:
        if len(self._extra_peer_source_lang_codes) >= self._MAX_EXTRA_LANGS:
            return
        self._extra_peer_source_lang_codes.append("ja")
        self._rebuild_extra_peer_src_rows()
        self._notify_language_change()

    def _on_remove_extra_peer_source(self, idx: int) -> None:
        if 0 <= idx < len(self._extra_peer_source_lang_codes):
            del self._extra_peer_source_lang_codes[idx]
        self._rebuild_extra_peer_src_rows()
        self._notify_language_change()

    def _open_extra_peer_source_dialog(self, idx: int = 0, _e=None):
        if idx >= len(self._extra_peer_source_lang_codes):
            return
        auto_label = t("language.auto", default="Auto Detect")
        langs = [("", auto_label)] + list(self._LANG_OPTIONS)
        modal = LanguageModal(page=self.page, languages=langs,
                              on_select=lambda code, i=idx: self._on_extra_peer_source_select(i, code))
        modal.open(current=self._extra_peer_source_lang_codes[idx], recent=self._recent_source_langs)

    def _on_extra_peer_source_select(self, idx: int, lang_code: str):
        if 0 <= idx < len(self._extra_peer_source_lang_codes):
            self._extra_peer_source_lang_codes[idx] = lang_code
        if lang_code:
            self._add_to_recent(lang_code, is_source=True)
        self._rebuild_extra_peer_src_rows()
        self._notify_language_change()

    def _rebuild_extra_peer_src_rows(self) -> None:
        _BTN_SLOT = 22
        rows: list[ft.Control] = []
        for i, lang_code in enumerate(self._extra_peer_source_lang_codes):
            display = language_name(lang_code) if lang_code else t("language.auto", default="Auto Detect")
            lbl = ft.Text(display, size=12, color=_TEXT_MUTED, no_wrap=True,
                          overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER, expand=True)
            arrow = ft.Icon(ft.Icons.CHEVRON_RIGHT, size=12, color=_TEXT_FAINT)
            card = ft.Container(
                content=ft.Row([lbl, arrow], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor="#2a2b2e", border_radius=6, border=ft.border.all(1, "#3a3b3e"),
                padding=ft.padding.symmetric(horizontal=8, vertical=5), expand=True,
                on_click=lambda _, idx=i: self._open_extra_peer_source_dialog(idx),
                on_hover=lambda e, l=lbl: (
                    setattr(l, "color", _RECV_COLOR if e.data == "true" else _TEXT_MUTED)
                    or (l.update() if l.page else None)
                ),
            )
            minus = ft.Container(
                content=ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
                on_click=lambda _, idx=i: self._on_remove_extra_peer_source(idx),
                tooltip=t("dashboard.tooltip.remove_peer_lang"), width=_BTN_SLOT,
            )
            rows.append(ft.Row([card, minus], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        self._extra_peer_src_rows_col.controls = rows
        # Show/hide the + slot next to the primary peer src card
        slot_visible = len(self._extra_peer_source_lang_codes) < self._MAX_EXTRA_LANGS
        self._peer_src_plus_slot.visible = slot_visible
        try:
            if self._extra_peer_src_rows_col.page:
                self._extra_peer_src_rows_col.update()
            if self._peer_src_plus_slot.page:
                self._peer_src_plus_slot.update()
        except Exception:
            pass

    # ── VRC mute sync toggle ─────────────────────────────────────────────────

    def _on_vrc_mute_sync_click(self, _=None) -> None:
        if not self.is_stt_on:
            return
        self._vrc_mute_sync = not self._vrc_mute_sync
        if self._vrc_mute_sync:
            self._vrc_mute_sync_osc_state = None  # reset synced state; wait for VRChat to re-send
            # Surface the relay risk up front (not just on hover): until VRChat sends the
            # mute state, the mic is transcribed and sent to chat even while muted.
            self._show_mute_sync_relay_warning()
        self._refresh_vrc_mute_sync_btn()
        if callable(self.on_vrc_mute_sync_toggle):
            self.on_vrc_mute_sync_toggle(self._vrc_mute_sync)

    def _show_mute_sync_relay_warning(self) -> None:
        # Defer slightly: enabling Mic Sync often coincides with the mic turning on and
        # the speech-model loading notice, and a snackbar fired in that busy moment can be
        # dropped or immediately overwritten. A short delay (re-checking it's still on)
        # makes the warning reliably appear.
        try:
            if self.page:
                self.page.run_task(self._delayed_mute_sync_warning)
        except Exception:
            pass

    async def _delayed_mute_sync_warning(self) -> None:
        import asyncio
        try:
            await asyncio.sleep(0.8)
        except Exception:
            return
        if not self._vrc_mute_sync or self._vrc_mute_sync_osc_state is not None:
            return  # disabled or already synced in the meantime
        try:
            if self.page:
                self.page.open(ft.SnackBar(
                    ft.Text(t("dashboard.mute_sync.warn_relay"), color=ft.Colors.WHITE),
                    bgcolor="#c47f1a",
                    duration=7000,
                    behavior=ft.SnackBarBehavior.FLOATING,
                    margin=ft.margin.only(bottom=90),
                    padding=20,
                ))
        except Exception:
            pass

    def _refresh_vrc_mute_sync_btn(self) -> None:
        active = self._vrc_mute_sync and self.is_stt_on
        btn = self._vrc_mute_sync_btn
        if not self.is_stt_on:
            # MIC is off — button is inert, show as fully dimmed
            btn.content.color = _TEXT_FAINT
            btn.bgcolor = ft.Colors.TRANSPARENT
            btn.border = ft.border.all(1, "#3a3b3f")
            btn.tooltip = t("dashboard.mute_sync.tooltip.off")
        elif active and self._vrc_mute_sync_osc_state is None:
            # Enabled but waiting for VRChat to send its mute state — show orange "syncing"
            _COLOR = "#e8a020"
            btn.content.color = _COLOR
            btn.bgcolor = "#2a1e08"
            btn.border = ft.border.all(1, _COLOR)
            btn.tooltip = t("dashboard.mute_sync.tooltip.syncing")
        elif active:
            btn.content.color = _TOGGLE_ON
            btn.bgcolor = "#1a2a1a"
            btn.border = ft.border.all(1, _TOGGLE_ON)
            btn.tooltip = t("dashboard.mute_sync.tooltip.active")
        else:
            btn.content.color = _TEXT_FAINT
            btn.bgcolor = ft.Colors.TRANSPARENT
            btn.border = ft.border.all(1, "#3a3b3f")
            btn.tooltip = t("dashboard.mute_sync.tooltip.off")
        try:
            btn.update()
        except Exception:
            pass

    def set_vrc_mute_sync_osc_state(self, muted: bool | None) -> None:
        """Called when VRChat OSC sends a mute state update."""
        self._vrc_mute_sync_osc_state = muted
        self._refresh_vrc_mute_sync_btn()

    # ── Peer voice to chatbox toggle ─────────────────────────────────────────

    def _on_chatbox_peer_btn_click(self, _=None) -> None:
        self._chatbox_send_peer = not self._chatbox_send_peer
        self._refresh_chatbox_peer_btn()
        if callable(self.on_chatbox_send_peer_toggle):
            self.on_chatbox_send_peer_toggle(self._chatbox_send_peer)

    def _on_loopback_right_click(self, e=None) -> None:
        # Same pill style as the OCR/overlay menus: option pills for the
        # all/selected pair (radio), On/Off pill for translation-only —
        # everything applies in place, menu stays open.
        x, y = self._tap_xy(e)

        # ── mode: summary button + inline radio expansion (mirrors the
        # overlay menu's Display/Size controls) ──
        def _mode_summary() -> str:
            return (t("dashboard.loopback.menu.mode.selected_short")
                    if self._loopback_selected_only
                    else t("dashboard.loopback.menu.mode.all_short"))

        _mode_btn_text = ft.Text(
            _mode_summary(), size=11, color=_TOGGLE_ON,
            weight=ft.FontWeight.W_600, no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        _mode_btn = ft.Container(
            content=_mode_btn_text,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6,
            bgcolor="#1a2e2a",
            border=ft.border.all(1, _TOGGLE_ON),
        )

        _mode_icons: dict[bool, ft.Icon] = {}
        _mode_rows: list[ft.Container] = []
        for _sel, _key in ((False, "dashboard.loopback.menu.all"),
                           (True, "dashboard.loopback.menu.selected")):
            _active = self._loopback_selected_only == _sel
            _icon = ft.Icon(
                ft.Icons.RADIO_BUTTON_CHECKED if _active
                else ft.Icons.RADIO_BUTTON_UNCHECKED,
                size=15, color=_TOGGLE_ON if _active else _TEXT_FAINT,
            )
            _mode_icons[_sel] = _icon

            def _on_mode_row(_ev, _s=_sel):
                if self._loopback_selected_only == _s:
                    return
                try:
                    self._set_loopback_mode(_s)
                except Exception:
                    import logging

                    logging.getLogger(__name__).exception(
                        "[Loopback] mode change failed")
                for _k, _ic in _mode_icons.items():
                    _on = _k == _s
                    _ic.name = (ft.Icons.RADIO_BUTTON_CHECKED if _on
                                else ft.Icons.RADIO_BUTTON_UNCHECKED)
                    _ic.color = _TOGGLE_ON if _on else _TEXT_FAINT
                _mode_btn_text.value = _mode_summary()
                with contextlib.suppress(Exception):
                    _mode_btn_text.update()
                    for _ic in _mode_icons.values():
                        _ic.update()

            _mode_rows.append(ft.Container(
                content=ft.Row(
                    [_icon,
                     ft.Text(t(_key), size=12, color=_TEXT_PRIMARY,
                             expand=True)],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.padding.only(left=18, right=10, top=6, bottom=6),
                border_radius=5,
                on_click=_on_mode_row,
                on_hover=lambda e: (
                    setattr(e.control, "bgcolor",
                            "#2a3040" if e.data == "true"
                            else ft.Colors.TRANSPARENT)
                    or (e.control.update() if e.control.page else None)
                ),
            ))

        _mode_rows_col = ft.Column(_mode_rows, spacing=0, tight=True,
                                   visible=False)
        _mode_expanded = [False]

        def _toggle_mode(_ev):
            _mode_expanded[0] = not _mode_expanded[0]
            _mode_rows_col.visible = _mode_expanded[0]
            with contextlib.suppress(Exception):
                _mode_rows_col.update()

        _mode_btn.on_click = _toggle_mode

        to_state = [self._loopback_translation_only]
        to_lbl = ft.Text(
            t("settings.option.on") if to_state[0] else t("settings.option.off"),
            size=11,
            color=_TOGGLE_ON if to_state[0] else _TEXT_FAINT,
            weight=ft.FontWeight.W_600,
        )
        to_pill = ft.Container(
            content=to_lbl,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6,
            border=ft.border.all(1, _TOGGLE_ON if to_state[0] else "#3a3b3f"),
        )

        def _to_click(_ev):
            to_state[0] = not to_state[0]
            to_lbl.value = (t("settings.option.on") if to_state[0]
                            else t("settings.option.off"))
            to_lbl.color = _TOGGLE_ON if to_state[0] else _TEXT_FAINT
            to_pill.border = ft.border.all(
                1, _TOGGLE_ON if to_state[0] else "#3a3b3f")
            with contextlib.suppress(Exception):
                to_lbl.update()
                to_pill.update()
            try:
                self._toggle_loopback_translation_only()
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "[Loopback] translation-only toggle failed")

        to_pill.on_click = _to_click

        content = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Row(
                            [ft.Text(t("dashboard.loopback.menu.mode"),
                                     size=11, color=_TEXT_MUTED),
                             ft.Container(
                                 content=ft.Icon(ft.Icons.INFO_OUTLINE,
                                                 size=11, color=_TEXT_FAINT),
                                 tooltip=t(
                                     "dashboard.loopback.menu.mode.tooltip"),
                                 padding=ft.padding.only(left=3),
                             ),
                             ft.Container(expand=True),
                             _mode_btn],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=ft.padding.only(left=10, right=10, top=8,
                                                bottom=2),
                    ),
                    _mode_rows_col,
                    ft.Container(
                        content=ft.Row(
                            [ft.Text(t("dashboard.loopback.menu.translation_only"),
                                     size=11, color=_TEXT_MUTED),
                             ft.Container(
                                 content=ft.Icon(ft.Icons.INFO_OUTLINE,
                                                 size=11, color=_TEXT_FAINT),
                                 tooltip=t(
                                     "dashboard.loopback.menu"
                                     ".translation_only.tooltip"),
                                 padding=ft.padding.only(left=3),
                             ),
                             ft.Container(expand=True),
                             to_pill],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=ft.padding.only(left=10, right=10, top=4,
                                                bottom=2),
                    ),
                    ft.Container(height=6),
                ],
                spacing=0,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )
        self._loopback_popover_close = self._open_popover_at(
            x, y, content, width=300.0)

    def _set_loopback_mode(self, selected_only: bool) -> None:
        self._loopback_selected_only = bool(selected_only)
        if callable(self.on_loopback_mode_change):
            self.on_loopback_mode_change(self._loopback_selected_only)

    def _toggle_loopback_translation_only(self) -> None:
        self._loopback_translation_only = not self._loopback_translation_only
        if callable(self.on_loopback_translation_only_change):
            self.on_loopback_translation_only_change(self._loopback_translation_only)

    def _refresh_chatbox_peer_btn(self) -> None:
        active = self._chatbox_send_peer
        btn = self._chatbox_peer_btn
        btn.content.color = _TOGGLE_ON if active else _TEXT_FAINT
        btn.bgcolor = "#1a2a1a" if active else ft.Colors.TRANSPARENT
        btn.border = ft.border.all(1, _TOGGLE_ON if active else "#3a3b3f")
        try:
            btn.update()
        except Exception:
            pass

    # ── Echo preview toggle ──────────────────────────────────────────────────

    def _on_echo_preview_toggle(self, _=None) -> None:
        self._show_pending_echo = not self._show_pending_echo
        btn = self._echo_preview_btn
        active = self._show_pending_echo
        btn.content.color = _TOGGLE_ON if active else _TEXT_FAINT
        btn.bgcolor = "#1a2a1a" if active else ft.Colors.TRANSPARENT
        btn.border = ft.border.all(1, _TOGGLE_ON if active else "#3a3b3f")
        try:
            btn.update()
        except Exception:
            pass

    def _swap_languages(self, _=None):
        self._source_lang_code, self._target_lang_code = self._target_lang_code, self._source_lang_code
        self._update_input_font()
        self._refresh_language_panel()
        self._refresh_language_rows()
        self._notify_language_change()

    def _swap_peer_languages(self, _=None):
        src = self._effective_peer_source_lang_code()
        tgt = self._effective_peer_target_lang_code()
        self._peer_source_lang_code = tgt
        self._peer_target_lang_code = src
        self._refresh_language_rows()
        self._notify_language_change()

    def _add_to_recent(self, lang_code: str, is_source: bool) -> None:
        recent = self._recent_source_langs if is_source else self._recent_target_langs
        if lang_code in recent:
            recent.remove(lang_code)
        recent.insert(0, lang_code)
        if len(recent) > 6:
            recent.pop()
        if self.on_recent_languages_change:
            self.on_recent_languages_change(self._recent_source_langs, self._recent_target_langs)

    def _notify_language_change(self):
        # TEMP diagnostic (r251): a language flip was observed at startup with
        # no user input — log the caller chain of every notify so the next
        # occurrence identifies itself. Cheap; remove once the phantom is found.
        try:
            import traceback as _tb
            _frames = _tb.extract_stack(limit=6)[:-1]
            logger.info("[LangNotify] via %s", " <- ".join(
                f"{f.name}:{f.lineno}" for f in reversed(_frames)))
        except Exception:
            pass
        if self.on_language_change:
            self.on_language_change(
                self._source_lang_code,
                self._target_lang_code,
                self._peer_source_lang_code,
                # Persist the RAW peer target — empty means "follow my language"
                # (the hub falls back to source). Persisting the *effective* value
                # would bake the current source in as a concrete pin, so e.g. being
                # on an English preset once would permanently pin peer→English even
                # after switching to a Chinese preset. See Hub._target_language_for.
                self._peer_target_lang_code,
                self._active_preset,
                list(self._extra_target_lang_codes),
                list(self._extra_peer_source_lang_codes),
            )

    def _effective_peer_source_lang_code(self) -> str:
        return self._peer_source_lang_code  # empty string = auto-detect by backend

    def _effective_peer_target_lang_code(self) -> str:
        return self._peer_target_lang_code or self._source_lang_code

    def _lang_card_tooltip(self, lang_code: str, *, kind: str) -> str:
        """Tooltip for a language card; explains what the language setting does.

        kind: "self" | "peer" | "target" | "peer_target".
        """
        if not lang_code:
            # Only spoken-language cards (self/peer) can be Auto Detect.
            return t("dashboard.lang.autodetect.peer" if kind == "peer" else "dashboard.lang.autodetect.self")
        key = {
            "self": "dashboard.lang.fixed.self",
            "peer": "dashboard.lang.fixed.peer",
            "target": "dashboard.lang.fixed.target",
            "peer_target": "dashboard.lang.fixed.peer_target",
        }.get(kind, "dashboard.lang.fixed.self")
        return t(key, language=language_name(lang_code))

    def _refresh_language_rows(self) -> None:
        src_name = language_name(self._effective_peer_source_lang_code())
        tgt_name = language_name(self._effective_peer_target_lang_code())
        self._peer_src_card.content.controls[0].value = src_name
        self._peer_tgt_card.content.controls[0].value = tgt_name
        self._peer_src_card.tooltip = self._lang_card_tooltip(self._peer_source_lang_code, kind="peer")
        self._peer_tgt_card.tooltip = self._lang_card_tooltip(
            self._effective_peer_target_lang_code(), kind="peer_target"
        )
        self._rebuild_extra_peer_tgt_rows()
        for ctrl in (self._peer_src_card, self._peer_tgt_card):
            try:
                ctrl.update()
            except Exception:
                pass
        # Show/hide the generic romanization toggle based on Auto Detect state.
        self._refresh_auto_translit()

    # ── Compatibility: old callers used language_card ─────────────────────────
    def _refresh_language_card(self) -> None:
        self._refresh_language_rows()

    # ── Public API ───────────────────────────────────────────────────────────

    def set_status(self, status: str) -> None:
        self.is_connected = status == "connected"
        self.display_card.set_status(status, font_family=self._ui_font())

    def set_languages_from_codes(
        self,
        source_code: str,
        target_code: str,
        peer_source_code: str = "",
        peer_target_code: str = "",
        active_preset: int = 0,
        presets: list[dict] | None = None,
    ) -> None:
        self._source_lang_code = source_code
        self._target_lang_code = target_code
        self._peer_source_lang_code = peer_source_code
        self._peer_target_lang_code = peer_target_code
        self._active_preset = max(0, min(active_preset, 2))
        if presets:
            self._preset_data = [
                {
                    "source": p.get("source", "en"),
                    "targets": p.get("targets", ["zh-CN"]),
                    "peer_source": p.get("peer_source", ""),
                    "peer_target": p.get("peer_target", ""),
                }
                for p in presets[:3]
            ]
            while len(self._preset_data) < 3:
                self._preset_data.append({"source": "en", "targets": ["en"]})
        # Restore extra targets and peer languages from active preset
        active = self._preset_data[self._active_preset]
        targets = active.get("targets", [target_code])
        self._extra_target_lang_codes = list(targets[1:])
        if active.get("peer_source", "") or active.get("peer_target", ""):
            self._peer_source_lang_code = active.get("peer_source", "")
            self._peer_target_lang_code = active.get("peer_target", "")
        # Unified view: the (hidden) text target mirrors the partner's
        # language. When the persisted target disagrees with the mirror (e.g.
        # settings written by an older build), push the corrected value back —
        # otherwise the hub keeps translating typed text into the stale
        # target. Converges: the second sync pass produces no diff.
        self._apply_unified_target_sync()
        if self._target_lang_code != target_code:
            self._notify_language_change()
        self._update_input_font()
        self._refresh_language_panel()
        self._refresh_language_rows()

    def set_translation_enabled(self, enabled: bool) -> None:
        self.is_translation_on = bool(enabled)
        if self.is_translation_on:
            self._translation_showing_warning = False
        self._sync_translation_button_state()

    def set_stt_enabled(self, enabled: bool) -> None:
        self.is_stt_on = bool(enabled)
        if self.is_stt_on:
            self._stt_showing_warning = False
        self._sync_stt_button_state()

    def set_overlay_peer_contract(self, contract: OverlayPeerConsumerContract) -> None:
        self._overlay_peer_contract = contract
        self._sync_overlay_peer_buttons()
        # At launch the model-loading notice can arrive BEFORE this contract does, so
        # the peer intent wasn't known yet and the loading ring stayed a grey dot.
        # Now that the intent is known, start the ring if a load is underway.
        row = getattr(self, "_row_peer", None)
        if (
            row is not None
            and not row.is_loading
            and self._local_stt_notice_status == "loading"
        ):
            self._drive_peer_loading_ring(None, "loading", None)

    def set_translation_needs_key(self, needs_key: bool, *, update_ui: bool = True) -> None:
        self.translation_needs_key = bool(needs_key)
        if update_ui and not self.is_translation_on:
            self._translation_showing_warning = bool(needs_key)
            self._sync_translation_button_state()

    def set_stt_needs_key(self, needs_key: bool, *, update_ui: bool = True) -> None:
        self.stt_needs_key = bool(needs_key)
        if update_ui and not self.is_stt_on:
            self._stt_showing_warning = bool(needs_key)
            self._sync_stt_button_state()

    def set_stt_error_state(self, error: bool) -> None:
        """Show red error dot on MIC button (e.g. model failed to load)."""
        self._stt_showing_error = bool(error)
        self._sync_stt_button_state()

    def set_peer_error_state(self, error: bool) -> None:
        """Show red error dot on PEER button (e.g. model failed to load)."""
        self._peer_showing_error = bool(error)
        self._sync_overlay_peer_buttons()

    def set_display_text(
        self,
        text: str,
        *,
        language_code: str | None = None,
        is_error: bool = False,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        utterance_id: object | None = None,
        channel: str | None = None,
        source_text_len: int | None = None,
        transcript_kind: str | None = None,
        should_log: bool = False,
        debug_prefix: str | None = None,
    ) -> None:
        # Untranslated finals reach the chat log via the hub's TRANSLATION_SKIPPED
        # event only. Appending here too printed every line twice with TRANS off
        # (this pre-r255 fallback + the r255 event were both writing).
        font_family = font_for_language(language_code) if language_code else self._ui_font()
        self.display_card.set_display(
            text,
            is_error=is_error,
            font_family=font_family,
            runtime_log_detailed=self.runtime_log_detailed,
            update_id=update_id,
            origin_wall_clock_ms=origin_wall_clock_ms,
            utterance_id=utterance_id,
            channel=channel,
            source_text_len=source_text_len,
            transcript_kind=transcript_kind,
            should_log=should_log,
            debug_prefix=debug_prefix,
        )

    def set_display_translation_text(
        self,
        text: str | None,
        *,
        language_code: str | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        utterance_id: object | None = None,
        channel: str | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
        debug_prefix: str | None = None,
    ) -> None:
        font_family = font_for_language(language_code) if language_code else self._ui_font()
        self.display_card.set_display_translation(
            text,
            font_family=font_family,
            runtime_log_detailed=self.runtime_log_detailed,
            update_id=update_id,
            origin_wall_clock_ms=origin_wall_clock_ms,
            utterance_id=utterance_id,
            channel=channel,
            session_scope=session_scope,
            source_text_hash=source_text_hash,
            source_text_len=source_text_len,
            logical_turn_key=logical_turn_key,
            debug_prefix=debug_prefix,
        )

    def set_managed_auth_pending(self, pending: bool) -> None:
        self._managed_auth_pending = bool(pending)
        self._sync_notice()

    def set_local_stt_notice(self, status: str | None, percent: int | None = None,
                             channel: str = "self") -> None:
        previous_status = self._local_stt_notice_status
        self._local_stt_notice_status = status
        self._local_stt_notice_percent = percent if status == "downloading" else None
        self._sync_notice()
        # The loading RING lives on the PEER row. Only a real PEER model load,
        # a shared model DOWNLOAD, or a clear should drive it — a SELF (mic)
        # model 'loading' must never paint the peer row.
        if channel == "peer" or status in ("downloading", None):
            self._drive_peer_loading_ring(previous_status, status, percent)

    # ── Determinate loading ring for the PEER row ────────────────────────────
    # Downloads report a real percent; the in-memory model LOAD is a single native
    # call with no progress signal, so we animate a time-based estimate (measured
    # from the previous load) that approaches ~95% and snaps to full when ready.

    def _peer_intent_enabled(self) -> bool:
        with contextlib.suppress(Exception):
            contract = self._overlay_peer_contract
            if contract is not None:
                return bool(contract.peer.intent_enabled)
        return False

    def _drive_peer_loading_ring(
        self, previous_status: str | None, status: str | None, percent: int | None
    ) -> None:
        row = getattr(self, "_row_peer", None)
        if row is None:
            return
        if status == "downloading":
            if row.is_loading and percent is not None:
                row.set_loading(True, max(1, min(100, percent)) / 100.0)
            return
        if status == "loading":
            if previous_status == "loading":
                return
            ring_was_visible = row.is_loading
            # Auto-show the ring when the model loads without a click (e.g. peer was
            # restored ON at launch). Optimistic when the contract hasn't arrived yet
            # (startup ordering): show the ring now rather than a second late; if peer
            # turns out to be off, the first contract sync flips it to the normal dot.
            intent_known = self._overlay_peer_contract is not None
            if not ring_was_visible and (not intent_known or self._peer_intent_enabled()):
                row.set_loading(True, 0.02)
            now = time.monotonic()
            resume_deadline = getattr(self, "_stt_load_resume_deadline", 0.0)
            started = getattr(self, "_stt_load_started_at", None)
            # Continue the previous fill only if the ring was actually ON SCREEN for
            # it — otherwise (e.g. it just auto-showed) start the clock fresh so the
            # ring rises from empty instead of popping in half-filled.
            if not ring_was_visible or not (started is not None and now < resume_deadline):
                self._stt_load_started_at = now
            self._stt_load_resume_deadline = 0.0
            if self.page is not None and row.is_loading:
                with contextlib.suppress(Exception):
                    self.page.run_task(self._animate_peer_load_ring)
            return
        if previous_status == "loading":
            # Don't snap/reset immediately: the self + peer models load back-to-back
            # and each fires its own loading cycle — a short grace window fuses them
            # into ONE continuous fill instead of the ring visibly restarting.
            self._stt_load_resume_deadline = time.monotonic() + 1.5
            if self.page is not None:
                with contextlib.suppress(Exception):
                    self.page.run_task(self._finish_peer_load_ring_after_grace)

    async def _finish_peer_load_ring_after_grace(self) -> None:
        await asyncio.sleep(1.5)
        if self._local_stt_notice_status == "loading":
            return  # another load resumed the pass — it will finish it
        row = getattr(self, "_row_peer", None)
        started = getattr(self, "_stt_load_started_at", None)
        if started is not None:
            self._last_stt_load_duration_s = max(
                0.5, min(30.0, time.monotonic() - started)
            )
            self._stt_load_started_at = None
        self._stt_load_resume_deadline = 0.0
        if row is not None and row.is_loading:
            row.set_loading(True, 1.0)
            # The full ring may only hold BRIEFLY: peer state syncs fire on changes
            # only, so if the peer sits in its idle/warning state, no set_state ever
            # arrives and the ring stays on screen forever (observed: stuck full
            # orange ring for 20+ minutes). Release to the real dot state shortly.
            self._peer_ring_release_gen = getattr(self, "_peer_ring_release_gen", 0) + 1
            if self.page is not None:
                with contextlib.suppress(Exception):
                    self.page.run_task(self._release_peer_ring_after_hold)

    async def _release_peer_ring_after_hold(self) -> None:
        generation = getattr(self, "_peer_ring_release_gen", 0)
        await asyncio.sleep(6.0)
        if generation != getattr(self, "_peer_ring_release_gen", 0):
            return  # a newer load pass owns the ring now
        if self._local_stt_notice_status == "loading":
            return
        row = getattr(self, "_row_peer", None)
        if row is None or not row.is_loading:
            return
        row.set_loading(False)
        # Restore the true contract-driven dot color (green when ready, orange while
        # becoming-ready/idle) instead of leaving the completed ring on screen.
        with contextlib.suppress(Exception):
            self._sync_overlay_peer_buttons()

    async def _animate_peer_load_ring(self) -> None:
        row = getattr(self, "_row_peer", None)
        if row is None or getattr(self, "_peer_ring_anim_running", False):
            return
        self._peer_ring_anim_running = True
        try:
            estimate = max(1.0, float(getattr(self, "_last_stt_load_duration_s", 4.0)))
            # Asymptotic fill: halves the remaining distance roughly every 45% of the
            # estimated duration — smooth, never stalls at a hard cap. The loop also
            # spans the grace window between back-to-back loads so the fill never dips.
            half_life = estimate * 0.45
            # 20 updates/s so the fill reads as continuous motion rather than stepping.
            while row.is_loading and (
                self._local_stt_notice_status == "loading"
                or time.monotonic() < getattr(self, "_stt_load_resume_deadline", 0.0)
            ):
                started = getattr(self, "_stt_load_started_at", None)
                if started is None:
                    break
                elapsed = time.monotonic() - started
                fraction = 1.0 - (0.5 ** (elapsed / half_life))
                row.set_loading(True, min(0.95, fraction))
                await asyncio.sleep(0.05)
        finally:
            self._peer_ring_anim_running = False
            # Belt-and-braces: however the animator exits (including toggle races that
            # bypass the grace/snap path), make sure the ring is eventually released
            # back to the contract-driven dot instead of sticking forever.
            self._peer_ring_release_gen = getattr(self, "_peer_ring_release_gen", 0) + 1
            if self.page is not None:
                with contextlib.suppress(Exception):
                    self.page.run_task(self._release_peer_ring_after_hold)

    def _current_local_stt_notice(self) -> tuple[str | None, str | None]:
        status = self._local_stt_notice_status
        if status is None:
            return None, None
        # "Loading speech model" shows NO banner at all anymore — the PEER
        # row's loading ring already communicates it (user preference).
        # Downloads/errors stay visible (rare and actionable).
        if status == "loading":
            return None, None
        notice_key_by_status = {
            "missing": "dashboard.local_stt_notice_missing",
            "invalid": "dashboard.local_stt_notice_invalid",
            "downloading": "dashboard.local_stt_notice_downloading",
            "download_failed": "dashboard.local_stt_notice_download_failed",
            "loading": "dashboard.local_stt_notice_loading",
        }
        tone_by_status = {
            "missing": "warning",
            "invalid": "warning",
            "downloading": "info",
            "download_failed": "error",
            "loading": "info",
        }
        notice_key = notice_key_by_status.get(status)
        if notice_key is None:
            return None, None
        notice_text = (
            t("dashboard.local_stt_notice_downloading_progress", percent=self._local_stt_notice_percent)
            if status == "downloading" and self._local_stt_notice_percent is not None
            else t(notice_key)
        )
        return notice_text, tone_by_status.get(status)

    def _current_overlay_failure_notice(self) -> tuple[str | None, str | None]:
        contract = self._overlay_peer_contract
        if contract is None:
            return None, None
        overlay = contract.overlay
        if overlay.state != "warning" or not overlay.failure_reason:
            return None, None
        status_text = t("settings.overlay.status.failed", default="failed")
        reason_text = t(f"settings.overlay.failure.{overlay.failure_reason}", default=overlay.failure_reason)
        if overlay.failure_reason in OVERLAY_FAILURE_REASON_ONLY_NOTICE_REASONS:
            return reason_text, "error"
        return (
            t("settings.overlay.status.failed_with_reason", status=status_text, reason=reason_text, default=f"{status_text}: {reason_text}"),
            "error",
        )

    def _sync_notice(self) -> None:
        # Also forward to hidden display_card for any controller that reads it
        if hasattr(self, "display_card"):
            if self._managed_auth_pending:
                self.display_card.set_notice(t("dashboard.managed_auth_pending"), "info")
            else:
                notice_text, tone = self._current_local_stt_notice()
                if notice_text is None:
                    notice_text, tone = self._current_overlay_failure_notice()
                self.display_card.set_notice(notice_text, tone)

        # Show notice in visible strip
        if not hasattr(self, "_notice_strip"):
            return
        if self._managed_auth_pending:
            self._show_notice(t("dashboard.managed_auth_pending"), "info")
            return
        notice_text, tone = self._current_local_stt_notice()
        if notice_text is not None:
            self._show_notice(notice_text, tone)
            return
        notice_text, tone = self._current_overlay_failure_notice()
        self._show_notice(notice_text, tone)

    def _show_notice(self, text: str | None, tone: str | None) -> None:
        if not hasattr(self, "_notice_strip"):
            return
        if not text:
            self._notice_strip.visible = False
            self._notice_download_btn.visible = False
        else:
            color = _TOGGLE_WARNING if tone == "warning" else (
                "#cf4040" if tone == "error" else _TOGGLE_ON
            )
            self._notice_text_ctrl.value = text
            self._notice_text_ctrl.color = color
            # Attached top bar; tone lives in the bottom border.
            self._notice_strip.bgcolor = "#2b3032"
            self._notice_strip.border = ft.border.only(
                bottom=ft.BorderSide(1, ft.Colors.with_opacity(0.55, color))
            )
            self._notice_strip.visible = True
            # Show download button when model is missing or download failed
            show_dl = self._local_stt_notice_status in ("missing", "invalid", "download_failed")
            self._notice_download_btn.visible = show_dl
        try:
            self._notice_strip.update()
        except Exception:
            pass

    def apply_locale(self) -> None:
        self._row_stt.set_label(t("dashboard.stt_label"))
        self._row_peer.set_label(t("dashboard.peer_label"))
        self._row_trans.set_label(t("dashboard.trans_label"))
        self._row_overlay.set_label(t("dashboard.overlay_label"))
        if hasattr(self, "_mini_stt_btn"):
            self._mini_stt_btn.set_tooltip(t("dashboard.stt_label"))
            self._mini_peer_btn.set_tooltip(t("dashboard.peer_label"))
            self._mini_trans_btn.set_tooltip(t("dashboard.trans_label"))
        self._sync_stt_button_state()
        self._sync_translation_button_state()
        self._sync_overlay_peer_buttons()
        self.display_card.apply_locale(
            display_font_family=self._ui_font(),
            input_font_family=None,
        )
        if hasattr(self, "_msg_input"):
            self._msg_input.hint_text = t("display.input_hint")
            try:
                self._msg_input.update()
            except Exception:
                pass
        for _attr, _key in (
            ("_lbl_your_language", "dashboard.your_language"),
            ("_lbl_translate_to", "dashboard.translate_to"),
            ("_lbl_you_speak", "dashboard.you_speak"),
            ("_chat_header_label", "dashboard.chat"),
        ):
            _lbl = getattr(self, _attr, None)
            if _lbl is not None:
                _lbl.value = t(_key)
                try:
                    _lbl.update()
                except Exception:
                    pass
        # Chat-header pill buttons are built once with t(); re-translate them here so a
        # runtime UI-language change updates them too (their refresh methods only touch
        # color/state, not the text).
        for _btn_attr, _btn_key in (
            ("_vrc_mute_sync_btn", "dashboard.button.mute_sync"),
            ("_chatbox_peer_btn", "dashboard.button.loopback"),
            ("_filter_peer_btn", "dashboard.button.target_langs_only"),
        ):
            _btn = getattr(self, _btn_attr, None)
            _content = getattr(_btn, "content", None)
            if _content is not None and hasattr(_content, "value"):
                _content.value = t(_btn_key)
                try:
                    _content.update()
                except Exception:
                    pass
        _ov_hdr = getattr(self, "_overlay_header_text", None)
        if _ov_hdr is not None:
            _ov_hdr.value = t("dashboard.button.overlay")
            try:
                _ov_hdr.update()
            except Exception:
                pass
        # "Peer voice" panel label + "Clear" chat button (both built inline / once).
        _peer_lbl = getattr(self, "_lbl_peer_voice", None)
        if _peer_lbl is not None:
            _peer_lbl.value = t("dashboard.language.peer")
            try:
                _peer_lbl.update()
            except Exception:
                pass
        _clear_btn = getattr(self, "_chat_clear_button", None)
        if _clear_btn is not None:
            _clear_btn.text = t("dashboard.clear")
            try:
                _clear_btn.update()
            except Exception:
                pass
        for _hdr, _key in getattr(self, "_section_header_labels", []):
            _hdr.value = t(_key)
            try:
                _hdr.update()
            except Exception:
                pass
        # Re-localize tooltips that were set once at construction. Flet tooltips don't
        # re-evaluate t() on a runtime UI-language change, so the MIC/PEER/TRANS rows and
        # the Loopback/Overlay buttons kept their startup-language hover text until now.
        # (Mute Sync, the overlay lock, and the VR/PC chip refresh via the _sync_* calls
        # above, which rebuild their state-dependent tooltips.)
        try:
            self._refresh_stt_tooltip()
            self._refresh_trans_tooltip()
            self._refresh_peer_tooltip()
        except Exception:
            pass
        for _ctrl, _key in getattr(self, "_static_tooltip_registry", []):
            try:
                _ctrl.tooltip = t(_key)
                _ctrl.update()
            except Exception:
                pass
        if hasattr(self, "_peer_src_card"):
            # Re-localize BOTH the self language cards/tabs (favorites panel) and the
            # peer row. Previously only the peer row refreshed on a UI-language change,
            # so the favorites' self source/target names stayed in the old language
            # until the user switched preset tabs and back (which calls both).
            self._refresh_language_panel()
            self._refresh_language_rows()
        if self._stt_showing_warning:
            self.set_display_text(t("dashboard.warn_stt_key"))
        elif self._translation_showing_warning:
            self.set_display_text(t("dashboard.warn_llm_key"))

    def set_recent_languages(self, source: list[str], target: list[str]) -> None:
        self._recent_source_langs = list(source)[:6]
        self._recent_target_langs = list(target)[:6]

    def _update_input_font(self) -> None:
        self.display_card.set_input_font(None)
        if hasattr(self, "_msg_input"):
            self._msg_input.text_style = ft.TextStyle(font_family="")
            try:
                self._msg_input.update()
            except Exception:
                pass

    def _ui_font(self) -> str | None:
        return font_for_language(get_locale())
