import datetime
from typing import Callable

import flet as ft

from puripuly_heart.core.language import get_all_language_options
from puripuly_heart.core.transliteration import transliterate_for_language
from puripuly_heart.ui.components.display_card import DisplayCard
from puripuly_heart.ui.components.language_modal import LanguageModal
from puripuly_heart.ui.components.settings import OptionItem, SettingsModal
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, language_name, t
from puripuly_heart.ui.overlay_peer_contract import OverlayPeerConsumerContract

_BUILD_TAG = "r87"  #increment each build so user can confirm version

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
        self._label_text = ft.Text(label, size=14, color=_TEXT_PRIMARY, expand=True)
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
                    self._label_text,
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

    def _on_hover(self, e):
        self.bgcolor = _BG_ROW_HOVER if e.data == "true" else _BG_SIDEBAR
        self.update()

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        self._spinner.visible = loading
        self._dot.visible = not loading
        try:
            self._indicator.update()
        except Exception:
            pass

    def set_state(self, on: bool, *, warning: bool = False, error: bool = False):
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
            tooltip="Swap languages",
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
        self.translation_needs_key = False
        self.stt_needs_key = False
        self.last_sent_text = t("dashboard.ready")
        self.history_items = []
        self._chat_entries: list[ft.Control] = []
        self._chat_list_view: ft.ListView | None = None
        self.single_turn_mode: bool = False

        self._pending_sent_col: ft.Column | None = None
        self._pending_version: int = 0
        self._show_pending_echo: bool = True  # on by default; toggled in Settings
        self._chatbox_send_peer: bool = False  # toggled in Settings and dashboard header
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

        # + button next to tgt1 card — always visible, adds another target language
        self._plus_btn = ft.Container(
            content=ft.Icon(ft.Icons.ADD_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
            on_click=self._on_add_extra_target,
            tooltip="Add target language",
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
            tooltip=(
                "Your spoken language (top card) — what you say.\n"
                "Your translation target (bottom card) — what it gets translated into.\n"
                "Tap either card to change. Use + to add a second target language."
            ),
            padding=ft.padding.only(left=4),
        )
        _your_lang_label_row = ft.Row(
            [
                ft.Text("Your Language", size=10, color="#c8c9cc"),
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
            tooltip="Add second spoken language (bilingual quick-switch)",
            visible=self._alt_source_lang_code is None,
            padding=ft.padding.only(left=4),
        )
        self._src_minus_btn = ft.Container(
            content=ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
            on_click=self._on_remove_alt_source,
            tooltip="Remove second spoken language",
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
        _translate_to_label = ft.Text("Translate to", size=10, color="#c8c9cc")
        lang_panel = ft.Container(
            content=ft.Column(
                [
                    _translate_to_label,
                    _tgt1_with_plus,
                    self._tgt1_translit_col,
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
            tooltip="Add peer target language",
            padding=ft.padding.only(left=4),
        )
        self._peer_src_card.expand = True
        self._peer_tgt_card.expand = True
        self._peer_src_plus_btn = ft.Container(
            content=ft.Icon(ft.Icons.ADD_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
            on_click=self._on_add_extra_peer_source,
            tooltip="Listen to another peer language",
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
        _you_speak_info = ft.Container(
            content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11, color=_TEXT_FAINT),
            tooltip="Your spoken language — sets the microphone (STT) recognition language.\nAlso used as source for text translation.\nClick to change; select Auto Detect to let it guess.",
            padding=ft.padding.only(left=2),
        )
        _peer_speaks_info = ft.Container(
            content=ft.Icon(ft.Icons.INFO_OUTLINE, size=11, color=_TEXT_FAINT),
            tooltip="The language your peer speaks.\nSet this so incoming audio translates correctly.\nAuto Detect works if you're unsure.",
            padding=ft.padding.only(left=2),
        )
        self._peer_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [ft.Text("You Speak", size=10, color="#c8c9cc"), _you_speak_info],
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    _src_with_plus,
                    self._alt_src_row,
                    ft.Divider(height=5, color=_DIVIDER, thickness=1),
                    ft.Row(
                        [ft.Text(t("dashboard.language.peer"), size=10, color="#c8c9cc"), _peer_speaks_info],
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
            tooltip="Change translation AI model (not microphone)",
            expand=True,
        )
        self.on_translator_change: object = None  # callback(model_value: str)
        self.on_stt_provider_change: object = None  # callback(provider_value: str)
        self.on_peer_stt_provider_change: object = None  # callback(provider_value: str)
        self._stt_provider_has_key: dict[str, bool] = {}  # provider_value → has key
        self._translator_model_has_key: dict[str, bool] = {}  # model_value → has key
        self.on_transliteration_change: object = None  # callback(show_pinyin, send_pinyin, show_romaji, send_romaji)
        self.on_overlay_lock_change: object = None  # callback(locked: bool)
        self.on_chatbox_send_peer_toggle: object = None  # callback(value: bool)
        self.on_self_in_overlay_toggle: object = None  # callback(value: bool) — spoken
        self.on_typed_in_overlay_toggle: object = None  # callback(value: bool) — typed
        self.on_vrc_mute_sync_toggle: object = None  # callback(value: bool)
        self.on_overlay_transparency_change: object = None  # callback(alpha: float)
        self._overlay_locked: bool = False
        self._overlay_background_alpha: float = 0.5

        self._sidebar_nav_row = ft.Container(
            content=ft.Row(
                [gear_btn],
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
            tooltip="Language settings — click to expand sidebar",
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
            tooltip="Collapse sidebar",
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

        def _section_card(icon: str, label: str, content: ft.Control) -> ft.Container:
            return ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(icon, size=15, color=_CARD_ICON_COLOR),
                                ft.Text(label, size=11, color=_TOGGLE_ON, weight=ft.FontWeight.W_700),
                            ],
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

        self._middle_section = ft.Column(
            [
                ft.Container(
                    content=self._preset_tabs_row,
                    padding=ft.padding.symmetric(horizontal=10, vertical=4),
                ),
                _section_card(ft.Icons.CHAT_BUBBLE_OUTLINE, "TEXT TRANSLATION", self._lang_panel),
                _section_card(ft.Icons.GRAPHIC_EQ, "VOICE TRANSLATION", self._peer_panel),
            ],
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
                    ft.Text("Download", size=11, color="#ffffff", weight=ft.FontWeight.W_600),
                ],
                spacing=3,
                tight=True,
            ),
            bgcolor=_TOGGLE_ON,
            border_radius=4,
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            on_click=lambda _: self.on_request_stt_download() if callable(self.on_request_stt_download) else None,
            visible=False,
            tooltip="Download the Qwen ASR local model (~980 MB)",
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
            bgcolor=ft.Colors.with_opacity(0.15, _TOGGLE_WARNING),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=10, vertical=6),
            visible=False,
        )

        # ── Chat log ─────────────────────────────────────────────────────────
        self._auto_scroll_enabled = True
        self._chat_list_view = ft.ListView(
            controls=self._chat_entries,
            expand=True,
            spacing=2,
            auto_scroll=True,
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
                "Target langs only",
                size=9,
                color=_RECV_COLOR,
                weight=ft.FontWeight.W_600,
            ),
            on_click=self._on_chat_filter_peer_click,
            tooltip="When on: only shows received messages from your configured Peer voice language(s)",
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            border_radius=10,
            bgcolor="#2d1f33",
            border=_pill_border_peer,
        )
        self._overlay_header_text = ft.Text(
            "Overlay", size=9, color=_TEXT_FAINT, weight=ft.FontWeight.W_600,
        )
        self._overlay_lock_icon = ft.Icon(ft.Icons.LOCK_OPEN, size=11, color=_TEXT_FAINT)
        _overlay_divider = ft.Container(
            width=1, height=12,
            bgcolor="#4a4b4f",
        )
        _overlay_left = ft.GestureDetector(
            content=ft.Container(
                content=self._overlay_header_text,
                on_click=self._on_overlay_click,
                tooltip="Toggle  |  Right-click: opacity",
                padding=ft.padding.only(left=8, right=6, top=3, bottom=3),
            ),
            on_secondary_tap=self._on_overlay_right_click,
        )
        self._overlay_lock_side = ft.Container(
            content=self._overlay_lock_icon,
            on_click=self._on_overlay_lock_click,
            tooltip="Lock overlay position",
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
        self._autoscroll_btn = ft.Container(
            content=ft.Text(
                "Auto-scroll",
                size=9,
                color=_TOGGLE_ON,
                weight=ft.FontWeight.W_600,
            ),
            on_click=self._on_autoscroll_toggle,
            tooltip="Toggle auto-scroll to latest message",
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            border_radius=10,
            bgcolor=ft.Colors.TRANSPARENT,
            border=_pill_border_on,
        )
        self._chatbox_peer_btn = ft.Container(
            content=ft.Text(
                "Loopback",
                size=9,
                color=_TEXT_FAINT,
                weight=ft.FontWeight.W_600,
            ),
            on_click=self._on_chatbox_peer_btn_click,
            tooltip="Send peer voice to VRChat chatbox (original + translation)",
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            border_radius=10,
            bgcolor=ft.Colors.TRANSPARENT,
            border=_pill_border_off,
        )
        self._vrc_mute_sync_btn = ft.Container(
            content=ft.Text(
                "Mute Sync",
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
        chat_header = ft.Row(
            [
                ft.Text("Chat", size=11, color=_TEXT_FAINT, weight=ft.FontWeight.W_500),
                ft.Container(expand=True),
                self._vrc_mute_sync_btn,
                ft.Container(width=4),
                self._chatbox_peer_btn,
                ft.Container(width=4),
                self._overlay_header_btn,
                ft.Container(width=4),
                self._autoscroll_btn,
                ft.Container(width=4),
                self._chat_clear_button,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
        )
        chat_box = ft.Container(
            content=self._chat_list_view,
            expand=True,
            bgcolor=_BG_CHAT,
            border_radius=6,
            padding=ft.padding.all(8),
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
                    self._notice_strip,
                    chat_header,
                    chat_box,
                    ft.Divider(height=1, color=_DIVIDER, thickness=1),
                    input_row,
                ],
                spacing=4,
                expand=True,
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
        enabled = True
        if self._overlay_peer_contract is not None:
            enabled = not self._overlay_peer_contract.overlay.intent_enabled
        if self.on_toggle_overlay:
            self.on_toggle_overlay(enabled)

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
        self._overlay_lock_icon.name = ft.Icons.LOCK if locked else ft.Icons.LOCK_OPEN
        self._overlay_lock_icon.color = _TOGGLE_ON if locked else _TEXT_FAINT
        self._overlay_lock_side.tooltip = "Unlock overlay position" if locked else "Lock overlay position"
        try:
            self._overlay_lock_icon.update()
        except Exception:
            pass

    def _on_overlay_right_click(self, e) -> None:
        alpha_label = ft.Text(
            f"{int(round(self._overlay_background_alpha * 100))}%",
            size=11,
            color=_TEXT_MUTED,
            text_align=ft.TextAlign.CENTER,
            width=40,
        )
        slider = ft.Slider(
            value=self._overlay_background_alpha,
            min=0.0,
            max=1.0,
            divisions=100,
            active_color=_TOGGLE_ON,
            inactive_color=_TOGGLE_OFF,
            thumb_color=_TOGGLE_ON,
        )

        def _on_change(ev):
            alpha = round(float(ev.control.value), 2)
            self._overlay_background_alpha = alpha
            alpha_label.value = f"{int(round(alpha * 100))}%"
            try:
                alpha_label.update()
            except Exception:
                pass
            if callable(self.on_overlay_transparency_change):
                self.on_overlay_transparency_change(alpha)

        slider.on_change = _on_change

        # "Show spoken messages" toggle row
        _spoken_label = ft.Text(
            "On" if self._self_in_overlay else "Off",
            size=11, color=_TOGGLE_ON if self._self_in_overlay else _TEXT_FAINT,
            weight=ft.FontWeight.W_600,
        )

        def _on_spoken_toggle(ev):
            self._self_in_overlay = not self._self_in_overlay
            _spoken_label.value = "On" if self._self_in_overlay else "Off"
            _spoken_label.color = _TOGGLE_ON if self._self_in_overlay else _TEXT_FAINT
            _spoken_border.border = ft.border.all(1, _TOGGLE_ON if self._self_in_overlay else "#3a3b3f")
            try:
                _spoken_label.update()
                _spoken_border.update()
            except Exception:
                pass
            if callable(self.on_self_in_overlay_toggle):
                self.on_self_in_overlay_toggle(self._self_in_overlay)

        _spoken_border = ft.Container(content=_spoken_label, on_click=_on_spoken_toggle,
                             padding=ft.padding.symmetric(horizontal=8, vertical=3),
                             border_radius=6,
                             border=ft.border.all(1, _TOGGLE_ON if self._self_in_overlay else "#3a3b3f"))

        _spoken_row = ft.Container(
            content=ft.Row(
                [
                    ft.Text("Show my voice messages", size=11, color=_TEXT_MUTED, expand=True),
                    _spoken_border,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.only(left=8, right=8, top=6, bottom=2),
        )

        # "Show typed messages" toggle row
        _typed_label = ft.Text(
            "On" if self._typed_in_overlay else "Off",
            size=11, color=_TOGGLE_ON if self._typed_in_overlay else _TEXT_FAINT,
            weight=ft.FontWeight.W_600,
        )

        def _on_typed_toggle(ev):
            self._typed_in_overlay = not self._typed_in_overlay
            _typed_label.value = "On" if self._typed_in_overlay else "Off"
            _typed_label.color = _TOGGLE_ON if self._typed_in_overlay else _TEXT_FAINT
            _typed_border.border = ft.border.all(1, _TOGGLE_ON if self._typed_in_overlay else "#3a3b3f")
            try:
                _typed_label.update()
                _typed_border.update()
            except Exception:
                pass
            if callable(self.on_typed_in_overlay_toggle):
                self.on_typed_in_overlay_toggle(self._typed_in_overlay)

        _typed_border = ft.Container(content=_typed_label, on_click=_on_typed_toggle,
                             padding=ft.padding.symmetric(horizontal=8, vertical=3),
                             border_radius=6,
                             border=ft.border.all(1, _TOGGLE_ON if self._typed_in_overlay else "#3a3b3f"))

        _typed_row = ft.Container(
            content=ft.Row(
                [
                    ft.Text("Show my text messages", size=11, color=_TEXT_MUTED, expand=True),
                    _typed_border,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.only(left=8, right=8, top=4, bottom=2),
        )

        dlg = ft.AlertDialog(
            modal=False,
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.OPACITY, size=13, color=_TEXT_MUTED),
                    ft.Text("Overlay Options", size=12, color=_TEXT_MUTED, weight=ft.FontWeight.W_600),
                ],
                spacing=6,
                tight=True,
            ),
            content=ft.Container(
                content=ft.Column(
                    [slider, alpha_label, _spoken_row, _typed_row],
                    spacing=0,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                width=220,
                bgcolor="#2e2f32",
            ),
            bgcolor="#2e2f32",
            content_padding=ft.padding.symmetric(horizontal=8, vertical=4),
            title_padding=ft.padding.only(left=16, top=14, right=16, bottom=0),
        )
        try:
            self.page.open(dlg)
        except Exception:
            pass

    def set_overlay_background_alpha(self, alpha: float) -> None:
        self._overlay_background_alpha = max(0.0, min(1.0, float(alpha)))

    def _on_autoscroll_toggle(self, e) -> None:
        self._auto_scroll_enabled = not self._auto_scroll_enabled
        self._chat_list_view.auto_scroll = self._auto_scroll_enabled
        btn = self._autoscroll_btn
        btn.content.color = _TOGGLE_ON if self._auto_scroll_enabled else _TEXT_FAINT
        btn.border = ft.border.all(1, _TOGGLE_ON if self._auto_scroll_enabled else "#3a3b3f")
        try:
            btn.update()
            self._chat_list_view.update()
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
    ) -> None:
        if self._chat_list_view is None:
            return
        import datetime as _dt
        timestamp = _dt.datetime.now().strftime("%H:%M")
        is_peer = channel == "peer"
        label_color = _RECV_COLOR if is_peer else _SENT_COLOR
        direction = t("dashboard.chat.received") if is_peer else t("dashboard.chat.sent")
        # Determine source/target language for transliteration
        if is_peer:
            src_lang = self._peer_source_lang_code  # may be "" (auto detect)
            tgt_lang = self._effective_peer_target_lang_code()  # always has a value
        else:
            src_lang = self._source_lang_code
            tgt_lang = self._target_lang_code

        _TRANSLIT_COLOR = "#5ba8a0"
        content_rows: list[ft.Control] = []
        has_translation = bool(source_text and translated_text and source_text.strip() != translated_text.strip())

        _want_romaji = self.show_romaji or self.send_romaji
        _want_pinyin = self.show_pinyin or self.send_pinyin
        _want_latin = self.show_latin or self.send_latin
        if has_translation:
            translit_src = transliterate_for_language(
                source_text, src_lang, show_pinyin=_want_pinyin, show_romaji=_want_romaji, show_latin=_want_latin
            )
            translit_tgt = transliterate_for_language(
                translated_text, tgt_lang, show_pinyin=_want_pinyin, show_romaji=_want_romaji, show_latin=_want_latin
            )
            # Source text with optional transliteration (if source is CJK)
            if translit_src:
                content_rows.append(ft.Text(translit_src, size=11, color=_TRANSLIT_COLOR, selectable=True, italic=True))
            content_rows.append(ft.Text(source_text.strip(), size=12, color=_TEXT_FAINT, selectable=True))
            # Translation block: pinyin/romaji above, then translation text
            if translit_tgt and translit_tgt != translit_src:
                content_rows.append(ft.Text(translit_tgt, size=11, color=_TRANSLIT_COLOR, selectable=True, italic=True))
            content_rows.append(ft.Text(translated_text.strip(), size=13, color=_TEXT_PRIMARY, selectable=True, weight=ft.FontWeight.W_500))
        elif translated_text:
            translit = transliterate_for_language(
                translated_text, tgt_lang, show_pinyin=_want_pinyin, show_romaji=_want_romaji, show_latin=_want_latin
            )
            if translit:
                content_rows.append(ft.Text(translit, size=11, color=_TRANSLIT_COLOR, selectable=True, italic=True))
            content_rows.append(ft.Text(translated_text.strip(), size=13, color=_TEXT_PRIMARY, selectable=True, weight=ft.FontWeight.W_500))
        else:
            content_rows.append(ft.Text(source_text.strip(), size=13, color=_TEXT_PRIMARY, selectable=True))

        # Header: just "Sent 16:37" — clean timestamp label
        header = ft.Row(
            [
                ft.Text(direction, size=11, color=label_color, weight=ft.FontWeight.W_700, selectable=True),
                ft.Text(f" {timestamp}", size=11, color=_TEXT_FAINT, selectable=True),
            ],
            spacing=0,
            tight=True,
        )

        # If a pending sent entry exists and this is a self-channel result, update it in-place
        if not is_peer and self._pending_sent_col is not None:
            self._pending_version += 1  # cancel timeout
            col = self._pending_sent_col
            self._pending_sent_col = None
            col.controls.clear()
            col.controls.extend([header, *content_rows])
            self._last_chat_content_col = col
            try:
                if self._chat_list_view.page:
                    self._chat_list_view.update()
                    if self._auto_scroll_enabled:
                        self._chat_list_view.scroll_to(offset=-1, duration=150)
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
            border=ft.border.only(left=ft.BorderSide(2, label_color)),
            margin=ft.margin.only(top=4),
            border_radius=ft.border_radius.only(top_right=4, bottom_right=4),
        )
        self._last_chat_content_col = entry.content  # track for extra-language appends
        self._chat_list_view.controls.append(entry)
        if len(self._chat_list_view.controls) > CHAT_MAX_ENTRIES:
            del self._chat_list_view.controls[:20]
        try:
            self._chat_list_view.update()
        except Exception:
            pass

    def append_extra_chat_lines(self, extra_pairs: list[tuple[str, str]]) -> None:
        """Append extra-language translation lines to the most recent chat entry."""
        col = getattr(self, "_last_chat_content_col", None)
        if col is None or self._chat_list_view is None:
            return
        _TRANSLIT_COLOR = "#5ba8a0"
        _want_romaji = self.show_romaji or self.send_romaji
        _want_pinyin = self.show_pinyin or self.send_pinyin
        _want_latin = self.show_latin or self.send_latin
        for lang_code, text in extra_pairs:
            if not text.strip():
                continue
            translit = transliterate_for_language(
                text, lang_code, show_pinyin=_want_pinyin, show_romaji=_want_romaji, show_latin=_want_latin
            )
            if translit:
                col.controls.append(ft.Text(translit, size=11, color=_TRANSLIT_COLOR, selectable=True, italic=True))
            col.controls.append(ft.Text(text.strip(), size=13, color=_TEXT_PRIMARY, selectable=True, weight=ft.FontWeight.W_500))
        try:
            self._chat_list_view.update()
        except Exception:
            pass

    def _append_raw_transcript(self, text: str, channel: str | None) -> None:
        """Add a raw STT-only line to chat (used when translation is disabled)."""
        if not text or not text.strip():
            return
        self.append_chat_entry(
            channel=channel or "self",
            source="stt",
            source_text=text.strip(),
            translated_text="",
        )

    # ── Submit / input ───────────────────────────────────────────────────────

    def _on_submit(self, text: str):
        self.set_display_text(text, language_code=self._source_lang_code)
        if self._chat_list_view is not None and self._show_pending_echo:
            import datetime as _dt
            timestamp = _dt.datetime.now().strftime("%H:%M")
            pending_text = ft.Text(text.strip(), size=13, color=_TEXT_FAINT, selectable=True, italic=True)
            pending_col = ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(t("dashboard.chat.sent"), size=11, color=_SENT_COLOR, weight=ft.FontWeight.W_700, selectable=True),
                            ft.Text(f" {timestamp}", size=11, color=_TEXT_FAINT, selectable=True),
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
                border=ft.border.only(left=ft.BorderSide(2, _SENT_COLOR)),
                margin=ft.margin.only(top=4),
                border_radius=ft.border_radius.only(top_right=4, bottom_right=4),
            )
            self._last_chat_content_col = pending_col
            self._chat_list_view.controls.append(entry)
            if len(self._chat_list_view.controls) > CHAT_MAX_ENTRIES:
                del self._chat_list_view.controls[:20]
            try:
                self._chat_list_view.update()
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
                        color="#e05050", size=11, italic=True, selectable=True,
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
                on_click=lambda _, idx=i: self._open_extra_target_dialog(idx),
                on_hover=lambda e, l=lbl: (
                    setattr(l, "color", _TOGGLE_ON if e.data == "true" else _TEXT_MUTED)
                    or (l.update() if l.page else None)
                ),
            )
            minus = ft.Container(
                content=ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
                on_click=lambda _, idx=i: self._on_remove_extra_target(idx),
                tooltip="Remove target language", width=_BTN_SLOT,
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
                on_click=lambda _, idx=i: self._open_extra_peer_target_dialog(idx),
                on_hover=lambda e, l=lbl: (
                    setattr(l, "color", _TOGGLE_ON if e.data == "true" else _TEXT_MUTED)
                    or (l.update() if l.page else None)
                ),
            )
            minus = ft.Container(
                content=ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINE, size=14, color=_TEXT_FAINT),
                on_click=lambda _, idx=i: self._on_remove_extra_peer_target(idx),
                tooltip="Remove peer target language", width=_BTN_SLOT,
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
        self._src_lang_card.tooltip = src_name
        self._tgt1_lang_card.content.controls[0].value = tgt1_name
        self._tgt1_lang_card.tooltip = tgt1_name
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
        self._row_trans.set_tooltip(f"Model: {label}\nRight-click to change")
        try:
            if self._translator_value_text.page:
                self._translator_value_text.update()
        except Exception:
            pass

    def _on_translator_btn_click(self, _=None) -> None:
        if not self.page:
            return
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
            TranslationModel.GOOGLE_TRANSLATE: "Google Translate (free)",
            TranslationModel.BING: "Bing (free)",
            TranslationModel.PAPAGO: "Papago (free)",
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
        options = []
        for m in _ORDERED_MODELS:
            needs_key = m in _NEEDS_KEY and not self._translator_model_has_key.get(m.value, False)
            desc = t("settings_modal.requires_api_key") if needs_key else ""
            options.append(OptionItem(value=m.value, label=_LABELS.get(m, m.value), description=desc, disabled=needs_key))
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

    def _build_stt_options(self) -> list:
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
            desc = t("settings_modal.requires_api_key") if needs_key else ""
            options.append(OptionItem(value=p.value, label=provider_label(p.value), description=desc, disabled=needs_key))
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
        SettingsModal(self.page, "Mic (STT)", self._build_stt_options(), self._on_stt_provider_selected, show_description=True).open(current)

    def _on_stt_provider_selected(self, value: str) -> None:
        if callable(self.on_stt_provider_change):
            self.on_stt_provider_change(value)

    def _on_peer_right_click(self, _=None) -> None:
        if not self.page:
            return
        from puripuly_heart.config.settings import STTProviderName
        current = getattr(self, "_current_peer_stt_provider_value", STTProviderName.LOCAL_QWEN.value)
        SettingsModal(self.page, "Peer Voice (STT)", self._build_stt_options(), self._on_peer_stt_provider_selected, show_description=True).open(current)

    def _on_peer_stt_provider_selected(self, value: str) -> None:
        if callable(self.on_peer_stt_provider_change):
            self.on_peer_stt_provider_change(value)

    def set_stt_provider_label(self, label: str, provider_value: str = "") -> None:
        if provider_value:
            self._current_stt_provider_value = provider_value
        self._refresh_stt_tooltip(label)

    def set_stt_input_device(self, device_name: str) -> None:
        self._stt_input_device = device_name or ""
        self._refresh_stt_tooltip()

    def _refresh_stt_tooltip(self, label: str | None = None) -> None:
        if label is None:
            label = getattr(self, "_current_stt_label", "")
        else:
            self._current_stt_label = label
        tip = f"Model: {label}\nRight-click to change"
        if self._stt_input_device:
            tip += f"\nDevice: {self._stt_input_device}"
        self._row_stt.set_tooltip(tip)

    def set_peer_stt_provider_label(self, label: str, provider_value: str = "") -> None:
        if provider_value:
            self._current_peer_stt_provider_value = provider_value
        self._row_peer.set_tooltip(f"Model: {label}\nRight-click to change")

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

    def _build_translit_col(self, lang_code: str) -> ft.Column:
        script = self._translit_script(lang_code)
        label = self._translit_label(script)
        show_val, send_val = self._translit_vals(script)

        def _chip(text: str, is_on: bool, cb) -> ft.Container:
            icon = ft.Icon(
                ft.Icons.CHECK_BOX if is_on else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                size=11,
                color=_TOGGLE_ON if is_on else _TEXT_FAINT,
            )
            lbl = ft.Text(text, size=10, color=_TEXT_MUTED if is_on else _TEXT_FAINT, no_wrap=True)
            c = ft.Container(
                content=ft.Row([icon, lbl], spacing=3, tight=True,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.padding.symmetric(horizontal=6, vertical=3),
                border_radius=4,
                bgcolor="#2a2b2e",
                border=ft.border.all(1, _TOGGLE_ON if is_on else "#3a3b3e"),
                on_click=lambda e, _cb=cb, _icon=icon, _lbl=lbl, _c=None: self._on_inline_chip_click(
                    e, _cb, icon, lbl
                ),
            )
            return c

        show_chip = _chip(f"Show {label}", show_val, self._translit_show_cb(script))
        send_chip = _chip(f"Send {label}", send_val, self._translit_send_cb(script))

        return ft.Column(
            [ft.Row([show_chip, send_chip], spacing=6, alignment=ft.MainAxisAlignment.START)],
            visible=script is not None,
            horizontal_alignment=ft.CrossAxisAlignment.START,
            spacing=0,
            data={"script": script, "show_chip": show_chip, "send_chip": send_chip},
        )

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
        return {"pinyin": "Pinyin", "romaji": "Romaji", "romaja": "Romaja"}.get(script or "", "Latin")

    def _translit_vals(self, script: str | None) -> tuple[bool, bool]:
        if script == "pinyin":
            return self.show_pinyin, self.send_pinyin
        if script in ("romaji", "romaja"):
            return self.show_romaji, self.send_romaji
        return self.show_latin, self.send_latin

    def _translit_show_cb(self, script: str | None):
        if script == "pinyin":
            return self._on_show_pinyin_toggle
        if script in ("romaji", "romaja"):
            return self._on_show_romaji_toggle
        return self._on_show_latin_toggle

    def _translit_send_cb(self, script: str | None):
        if script == "pinyin":
            return self._on_send_pinyin_toggle
        if script in ("romaji", "romaja"):
            return self._on_send_romaji_toggle
        return self._on_send_latin_toggle

    def _refresh_translit_col(self, col: ft.Column, lang_code: str) -> None:
        script = self._translit_script(lang_code)
        col.visible = script is not None
        if script is None:
            return
        label = self._translit_label(script)
        show_val, send_val = self._translit_vals(script)
        d = col.data
        # Update script + callbacks if language type changed
        old_script = d.get("script")
        if old_script != script:
            show_cb = self._translit_show_cb(script)
            send_cb = self._translit_send_cb(script)
            d["script"] = script
            # Rewire callbacks via closure — update chip on_click
            show_chip: ft.Container = d["show_chip"]
            send_chip: ft.Container = d["send_chip"]
            show_icon = show_chip.content.controls[0]
            show_lbl = show_chip.content.controls[1]
            send_icon = send_chip.content.controls[0]
            send_lbl = send_chip.content.controls[1]
            show_chip.on_click = lambda e, _cb=show_cb, _i=show_icon, _l=show_lbl: \
                self._on_inline_chip_click(e, _cb, _i, _l)
            send_chip.on_click = lambda e, _cb=send_cb, _i=send_icon, _l=send_lbl: \
                self._on_inline_chip_click(e, _cb, _i, _l)
        else:
            show_chip = d["show_chip"]
            send_chip = d["send_chip"]
            show_icon = show_chip.content.controls[0]
            show_lbl = show_chip.content.controls[1]
            send_icon = send_chip.content.controls[0]
            send_lbl = send_chip.content.controls[1]
        # Sync chip labels
        show_lbl.value = f"Show {label}"
        send_lbl.value = f"Send {label}"
        # Sync chip states
        for chip, icon, lbl, val in [
            (show_chip, show_icon, show_lbl, show_val),
            (send_chip, send_icon, send_lbl, send_val),
        ]:
            icon.name = ft.Icons.CHECK_BOX if val else ft.Icons.CHECK_BOX_OUTLINE_BLANK
            icon.color = _TOGGLE_ON if val else _TEXT_FAINT
            lbl.color = _TEXT_MUTED if val else _TEXT_FAINT
            chip.border = ft.border.all(1, _TOGGLE_ON if val else "#3a3b3e")

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

    def _open_peer_source_dialog(self, _=None):
        auto_label = t("language.auto", default="Auto Detect")
        peer_src_langs = [("", auto_label)] + list(self._LANG_OPTIONS)
        modal = LanguageModal(page=self.page, languages=peer_src_langs, on_select=self._on_peer_source_select)
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

    def _on_source_select(self, lang_code: str):
        self._source_lang_code = lang_code
        if lang_code:  # don't add "Auto" to recent
            self._add_to_recent(lang_code, is_source=True)
        self._update_input_font()
        self._refresh_language_panel()
        self._refresh_language_rows()
        self._notify_language_change()

    def _on_target_select(self, lang_code: str):
        self._target_lang_code = lang_code
        self._add_to_recent(lang_code, is_source=False)
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
        self._refresh_language_rows()
        self._notify_language_change()

    def _on_peer_target_select(self, lang_code: str):
        self._peer_target_lang_code = "" if lang_code == self._target_lang_code else lang_code
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
                tooltip="Remove peer language", width=_BTN_SLOT,
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
        self._refresh_vrc_mute_sync_btn()
        if callable(self.on_vrc_mute_sync_toggle):
            self.on_vrc_mute_sync_toggle(self._vrc_mute_sync)

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
        if self.on_language_change:
            self.on_language_change(
                self._source_lang_code,
                self._target_lang_code,
                self._peer_source_lang_code,
                self._effective_peer_target_lang_code(),
                self._active_preset,
                list(self._extra_target_lang_codes),
            )

    def _effective_peer_source_lang_code(self) -> str:
        return self._peer_source_lang_code  # empty string = auto-detect by backend

    def _effective_peer_target_lang_code(self) -> str:
        return self._peer_target_lang_code or self._source_lang_code

    def _refresh_language_rows(self) -> None:
        src_name = language_name(self._effective_peer_source_lang_code())
        tgt_name = language_name(self._effective_peer_target_lang_code())
        self._peer_src_card.content.controls[0].value = src_name
        self._peer_tgt_card.content.controls[0].value = tgt_name
        self._peer_src_card.tooltip = src_name
        self._peer_tgt_card.tooltip = tgt_name
        self._rebuild_extra_peer_tgt_rows()
        for ctrl in (self._peer_src_card, self._peer_tgt_card):
            try:
                ctrl.update()
            except Exception:
                pass

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
        # When translation is off, route final transcripts to the chat log directly
        if not is_error and transcript_kind == "final" and not self.is_translation_on and text and text.strip():
            self._append_raw_transcript(text, channel)
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

    def set_local_stt_notice(self, status: str | None, percent: int | None = None) -> None:
        self._local_stt_notice_status = status
        self._local_stt_notice_percent = percent if status == "downloading" else None
        self._sync_notice()

    def _current_local_stt_notice(self) -> tuple[str | None, str | None]:
        status = self._local_stt_notice_status
        if status is None:
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
            self._notice_strip.bgcolor = ft.Colors.with_opacity(0.15, color)
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
        if hasattr(self, "_peer_src_card"):
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
