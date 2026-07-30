from __future__ import annotations

import logging

import pytest

ft = pytest.importorskip("flet")

from puripuly_heart.ui.overlay_peer_contract import (
    OverlayPeerConsumerContract,
    OverlayPeerToggleContract,
)
from puripuly_heart.ui.views import dashboard as dashboard_module
from tests.helpers.flet_page import attach_dummy_page


class FakeDisplayCard:
    def __init__(self, on_submit, on_input_focus_change=None):
        self._on_submit = on_submit
        self._on_input_focus_change = on_input_focus_change
        self.statuses: list[tuple[str, str | None]] = []
        self.display_calls: list[tuple[str, bool, str | None]] = []
        self.translation_calls: list[tuple[str | None, str | None]] = []
        self.translation_metadata_calls: list[dict[str, object]] = []
        self.notice_calls: list[tuple[str | None, str | None]] = []
        self.input_fonts: list[str | None] = []
        self.locale_calls: list[tuple[str | None, str | None]] = []
        self.input_is_focused = False
        self.focus_calls = 0

    def set_status(self, status: str, font_family: str | None = None) -> None:
        self.statuses.append((status, font_family))

    def set_display(
        self,
        text: str,
        *,
        is_error: bool = False,
        font_family: str | None = None,
        **_metadata,
    ) -> None:
        self.display_calls.append((text, is_error, font_family))

    def set_display_translation(
        self,
        text: str | None,
        font_family: str | None = None,
        **metadata,
    ) -> None:
        self.translation_calls.append((text, font_family))
        self.translation_metadata_calls.append(dict(metadata))

    def set_notice(self, text: str | None, tone: str | None = None) -> None:
        self.notice_calls.append((text, tone))

    def set_input_font(self, font_family: str | None) -> None:
        self.input_fonts.append(font_family)

    def apply_locale(self, display_font_family: str | None, input_font_family: str | None) -> None:
        self.locale_calls.append((display_font_family, input_font_family))

    def set_input_focus_for_test(self, focused: bool) -> None:
        self.input_is_focused = focused
        if self._on_input_focus_change is not None:
            self._on_input_focus_change(focused)

    def focus_input(self) -> None:
        self.focus_calls += 1


class FakeLanguageModal:
    opened: list[tuple[str, list[str]]] = []

    def __init__(self, page, languages, on_select):
        _ = (page, languages)
        self.on_select = on_select

    def open(self, *, current: str, recent: list[str]) -> None:
        self.__class__.opened.append((current, list(recent)))


def _make_dashboard(monkeypatch: pytest.MonkeyPatch):
    # DashboardView.__init__ reads the real user settings.json for the
    # unified-translation / auto-detect prefs — point "~" at a non-existent
    # home so every test run exercises the shipped defaults.
    monkeypatch.setenv("USERPROFILE", r"C:\puripuly-heart-tests-no-home")
    monkeypatch.setenv("HOME", r"C:\puripuly-heart-tests-no-home")
    monkeypatch.setattr(dashboard_module, "DisplayCard", FakeDisplayCard)
    monkeypatch.setattr(dashboard_module, "LanguageModal", FakeLanguageModal)
    monkeypatch.setattr(dashboard_module, "font_for_language", lambda code: f"font-{code}")
    monkeypatch.setattr(
        dashboard_module,
        "language_name",
        lambda code: f"name-{code}" if code else "Auto Detect",
    )
    monkeypatch.setattr(dashboard_module, "get_locale", lambda: "en")
    view = dashboard_module.DashboardView()
    FakeLanguageModal.opened = []
    return view


def _row_label(row) -> str:
    return row._label_text.value


def _row_icon(row) -> str:
    return row.content.controls[0].name


def _row_dot(row) -> str:
    return row._dot.bgcolor


def _card_text(card) -> str:
    return card.content.controls[0].value


def _make_overlay_peer_contract(
    *,
    overlay_intent_enabled: bool,
    overlay_state: str,
    overlay_status_text: str,
    overlay_helper_text: str = "",
    peer_intent_enabled: bool,
    peer_effective_enabled: bool,
    peer_status_text: str,
    peer_helper_text: str = "",
) -> OverlayPeerConsumerContract:
    return OverlayPeerConsumerContract(
        overlay=OverlayPeerToggleContract(
            intent_enabled=overlay_intent_enabled,
            effective_enabled=overlay_state == "connected",
            action_enabled=True,
            state=(
                "on"
                if overlay_state == "connected"
                else ("off" if not overlay_intent_enabled else "warning")
            ),
            status_text=overlay_status_text,
            helper_text=overlay_helper_text,
        ),
        peer=OverlayPeerToggleContract(
            intent_enabled=peer_intent_enabled,
            effective_enabled=peer_effective_enabled,
            action_enabled=True,
            state=(
                "on"
                if peer_effective_enabled
                else ("off" if not peer_intent_enabled else "warning")
            ),
            status_text=peer_status_text,
            helper_text=peer_helper_text,
        ),
    )


def test_dashboard_initial_peer_language_defaults_to_auto_detect_and_follow_self(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)

    assert view._source_lang_code == "ko"
    assert view._target_lang_code == "en"
    assert view._peer_source_lang_code == ""
    assert view._peer_target_lang_code == ""
    assert view._effective_peer_target_lang_code() == "ko"
    assert _card_text(view._src_lang_card) == "name-ko"
    assert _card_text(view._tgt1_lang_card) == "name-en"
    assert _card_text(view._peer_src_card) == "Auto Detect"
    assert _card_text(view._peer_tgt_card) == "name-ko"


def test_dashboard_stt_toggle_warning_and_enable_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    seen: list[bool] = []
    view.on_toggle_stt = lambda enabled: seen.append(enabled)
    view.stt_needs_key = True

    view._toggle_stt()
    view._toggle_stt()
    view.stt_needs_key = False
    view._toggle_stt()
    view._toggle_stt()

    assert seen == [False, False, True, False]
    assert view.is_stt_on is False
    assert view._stt_showing_warning is False
    assert any(
        call[0] == dashboard_module.t("dashboard.warn_stt_key")
        for call in view.display_card.display_calls
    )


def test_dashboard_translation_toggle_controls_power_state(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    seen: list[bool] = []
    view.on_toggle_translation = lambda enabled: seen.append(enabled)
    view.translation_needs_key = True

    # Translation starts ON by default, so the first toggle turns it off; the
    # next toggle hits the missing-key warning, the one after acknowledges it,
    # and the final toggle (key present) turns translation back on.
    view._toggle_translation()
    view._toggle_translation()
    view.translation_needs_key = False
    view._toggle_translation()
    view._toggle_translation()

    assert seen == [False, False, False, True]
    assert view.is_translation_on is True
    assert view.is_power_on is True
    assert any(
        call[0] == dashboard_module.t("dashboard.warn_llm_key")
        for call in view.display_card.display_calls
    )


def test_dashboard_translation_visual_commit_forwards_metadata_and_runtime_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)

    def fake_runtime_log_detailed(message: str, *, level: int = logging.INFO) -> bool:
        _ = (message, level)
        return True

    view.runtime_log_detailed = fake_runtime_log_detailed

    view.set_display_translation_text(
        "dst",
        language_code="en",
        update_id="upd-1",
        origin_wall_clock_ms=1712345678901,
        utterance_id="utt-1",
        channel="peer",
        session_scope="session-1",
        source_text_hash="src-hash-1",
        source_text_len=12,
        logical_turn_key="peer:utt-1",
    )

    assert view.display_card.translation_calls[-1] == ("dst", "font-en")
    assert view.display_card.translation_metadata_calls[-1] == {
        "runtime_log_detailed": fake_runtime_log_detailed,
        "update_id": "upd-1",
        "origin_wall_clock_ms": 1712345678901,
        "utterance_id": "utt-1",
        "channel": "peer",
        "session_scope": "session-1",
        "source_text_hash": "src-hash-1",
        "source_text_len": 12,
        "logical_turn_key": "peer:utt-1",
        "debug_prefix": None,
    }


def test_dashboard_submit_and_language_selection_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    sends: list[tuple[str, str]] = []
    lang_changes: list[tuple] = []
    view.on_send_message = lambda source, text: sends.append((source, text))
    view.on_language_change = lambda *args: lang_changes.append(args)

    view._on_submit("hello")
    view._on_source_select("ja")
    view._on_target_select("fr")
    view._swap_languages()

    assert sends == [("You", "hello")]
    assert view._recent_source_langs == ["ja"]
    assert view._recent_target_langs == ["fr"]
    assert lang_changes[-1] == ("fr", "ja", "", "", 0, [], [])
    assert _card_text(view._src_lang_card) == "name-fr"
    assert _card_text(view._tgt1_lang_card) == "name-ja"


def test_dashboard_tab_in_focused_message_input_swaps_self_languages_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple] = []
    view.on_language_change = lambda *args: changes.append(args)
    # Unified view mirrors the typed-output target onto the peer's language,
    # so ko/en/ja/fr settles as source=ko target=ja.
    view.set_languages_from_codes("ko", "en", "ja", "fr")
    view.display_card.set_input_focus_for_test(True)

    handled = view.handle_message_input_tab_key()

    assert handled is True
    assert view._source_lang_code == "ja"
    assert view._target_lang_code == "ko"
    assert view._peer_source_lang_code == "ja"
    assert view._peer_target_lang_code == "fr"
    assert changes[-1] == ("ja", "ko", "ja", "fr", 0, [], [])


def test_dashboard_tab_ignored_when_message_input_is_not_focused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple] = []
    view.on_language_change = lambda *args: changes.append(args)
    view.set_languages_from_codes("ko", "en", "ja", "fr")
    changes_after_setup = len(changes)
    view.display_card.set_input_focus_for_test(False)

    handled = view.handle_message_input_tab_key()

    assert handled is False
    assert view._source_lang_code == "ko"
    assert view._target_lang_code == "ja"
    assert len(changes) == changes_after_setup


def test_dashboard_recent_languages_caps_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    persisted: list[tuple[list[str], list[str]]] = []
    view.on_recent_languages_change = lambda src, tgt: persisted.append((list(src), list(tgt)))

    for idx in range(8):
        view._add_to_recent(f"s{idx}", is_source=True)
        view._add_to_recent(f"t{idx}", is_source=False)

    assert len(view._recent_source_langs) == 6
    assert len(view._recent_target_langs) == 6
    assert view._recent_source_langs[0] == "s7"
    assert view._recent_source_langs[-1] == "s2"
    assert persisted


def test_dashboard_public_setters_update_components(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    view.set_status("connected")
    view.set_languages_from_codes("ko", "en")
    view.set_translation_enabled(False)
    view.set_stt_enabled(False)
    view.set_translation_needs_key(True, update_ui=True)
    view.set_stt_needs_key(True, update_ui=True)
    view.set_local_stt_notice("missing")
    view.set_managed_auth_pending(True)
    view.set_display_text("src", language_code="ko")
    view.set_display_translation_text("dst", language_code="en")
    view.set_recent_languages(["a", "b", "c", "d", "e", "f", "g"], ["x", "y", "z"])

    assert view.is_connected is True
    assert view.display_card.statuses[-1] == ("connected", "font-en")
    assert view.display_card.display_calls[-1] == ("src", False, "font-ko")
    assert view.display_card.translation_calls[-1] == ("dst", "font-en")
    assert view.display_card.notice_calls[-1] == (
        dashboard_module.t("dashboard.managed_auth_pending"),
        "info",
    )
    assert view._source_lang_code == "ko"
    assert view._target_lang_code == "en"
    assert _card_text(view._src_lang_card) == "name-ko"
    assert _card_text(view._tgt1_lang_card) == "name-en"
    assert view.trans_button._state is False
    assert view.trans_button._warning is True
    assert _row_dot(view.trans_button) == dashboard_module._TOGGLE_WARNING
    assert view.stt_button._state is False
    assert view.stt_button._warning is True
    assert _row_dot(view.stt_button) == dashboard_module._TOGGLE_WARNING
    assert view._recent_source_langs == ["a", "b", "c", "d", "e", "f"]


def test_dashboard_managed_auth_pending_restores_local_stt_notice_when_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)

    view.set_local_stt_notice("missing")
    view.set_managed_auth_pending(True)
    view.set_managed_auth_pending(False)

    assert view.display_card.notice_calls == [
        (dashboard_module.t("dashboard.local_stt_notice_missing"), "warning"),
        (dashboard_module.t("dashboard.managed_auth_pending"), "info"),
        (dashboard_module.t("dashboard.local_stt_notice_missing"), "warning"),
    ]


def test_dashboard_apply_locale_reapplies_managed_auth_pending_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)

    view.set_managed_auth_pending(True)
    view.apply_locale()

    # apply_locale re-syncs the notice through both the STT-row and the
    # overlay/peer-row paths, so the pending notice is re-emitted at least
    # once — and nothing else ever wins while auth is pending.
    pending_notice = (dashboard_module.t("dashboard.managed_auth_pending"), "info")
    assert view.display_card.notice_calls[0] == pending_notice
    assert len(view.display_card.notice_calls) > 1
    assert set(view.display_card.notice_calls) == {pending_notice}


def test_dashboard_builds_vrct_shell_without_managed_trial_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)

    assert len(view.controls) == 2
    assert view.controls[0] is view._sidebar_container
    assert view.stt_button is view._row_stt
    assert view.peer_button is view._row_peer
    assert view.trans_button is view._row_trans
    assert view.overlay_button is view._row_overlay
    assert _row_label(view.stt_button) == dashboard_module.t("dashboard.stt_label")
    assert _row_label(view.peer_button) == dashboard_module.t("dashboard.peer_label")
    assert _row_label(view.trans_button) == dashboard_module.t("dashboard.trans_label")
    assert _row_label(view.overlay_button) == dashboard_module.t("dashboard.overlay_label")
    assert _row_icon(view.overlay_button) == ft.Icons.SUBTITLES
    # The display card survives only as a hidden controller-API shim.
    assert view.display_card.visible is False
    assert not hasattr(view, "_managed_trial_card")


def test_dashboard_sidebar_rows_use_trans_and_subtitles_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)

    assert _row_label(view.trans_button) == "TRANS"
    assert _row_label(view.overlay_button) == "Subtitles"


def test_dashboard_overlay_button_uses_subtitles_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)

    assert _row_icon(view.overlay_button) == ft.Icons.SUBTITLES


def test_dashboard_peer_trans_overlay_buttons_use_default_on_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)

    view.set_translation_enabled(True)
    view.set_overlay_peer_contract(
        _make_overlay_peer_contract(
            overlay_intent_enabled=True,
            overlay_state="connected",
            overlay_status_text="Overlay on",
            peer_intent_enabled=True,
            peer_effective_enabled=True,
            peer_status_text="Peer on",
        )
    )

    assert _row_dot(view.trans_button) == dashboard_module._TOGGLE_ON
    assert _row_dot(view.peer_button) == dashboard_module._TOGGLE_ON
    assert _row_dot(view.overlay_button) == dashboard_module._TOGGLE_ON


def test_dashboard_apply_locale_and_dialog_open_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    attach_dummy_page(monkeypatch, view)
    view._stt_showing_warning = True
    view._open_source_dialog()
    view._open_target_dialog()
    view.apply_locale()
    view._translation_showing_warning = True
    view._stt_showing_warning = False
    view.apply_locale()

    assert FakeLanguageModal.opened[0][0] == "ko"
    assert FakeLanguageModal.opened[1][0] == "en"
    assert _row_label(view.stt_button) == dashboard_module.t("dashboard.stt_label")
    assert _row_label(view.peer_button) == dashboard_module.t("dashboard.peer_label")
    assert _row_label(view.trans_button) == dashboard_module.t("dashboard.trans_label")
    assert _row_label(view.overlay_button) == dashboard_module.t("dashboard.overlay_label")
    warning_texts = [text for text, _is_error, _font in view.display_card.display_calls]
    assert dashboard_module.t("dashboard.warn_stt_key") in warning_texts
    assert dashboard_module.t("dashboard.warn_llm_key") in warning_texts


def test_dashboard_overlay_peer_buttons_render_consumer_contract_state_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    contract = _make_overlay_peer_contract(
        overlay_intent_enabled=True,
        overlay_state="failed",
        overlay_status_text="Overlay failed",
        overlay_helper_text="Overlay helper copy",
        peer_intent_enabled=True,
        peer_effective_enabled=False,
        peer_status_text="Peer waiting",
        peer_helper_text="Overlay is starting",
    )

    view.set_overlay_peer_contract(contract)

    assert view.overlay_button._state is False
    assert view.overlay_button._warning is True
    assert _row_dot(view.overlay_button) == dashboard_module._TOGGLE_WARNING
    assert view.peer_button._state is False
    assert view.peer_button._warning is True
    assert _row_dot(view.peer_button) == dashboard_module._TOGGLE_WARNING


def test_dashboard_overlay_failure_notice_is_lowest_priority_notice_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    overlay_failure_notice = dashboard_module.t(
        "settings.overlay.status.failed_with_reason",
        status=dashboard_module.t("settings.overlay.status.failed"),
        reason=dashboard_module.t(
            "settings.overlay.failure.runtime_unavailable",
            default="runtime_unavailable",
        ),
        default="Overlay failed: runtime_unavailable",
    )

    view.set_overlay_peer_contract(
        OverlayPeerConsumerContract(
            overlay=OverlayPeerToggleContract(
                intent_enabled=True,
                effective_enabled=False,
                action_enabled=True,
                state="warning",
                status_text="Failed: runtime unavailable",
                failure_reason="runtime_unavailable",
            ),
            peer=OverlayPeerToggleContract(
                intent_enabled=False,
                effective_enabled=False,
                action_enabled=True,
                state="off",
                status_text="Off",
            ),
        )
    )
    view.set_local_stt_notice("missing")
    view.set_managed_auth_pending(True)
    view.set_managed_auth_pending(False)
    view.set_local_stt_notice(None)

    assert view.display_card.notice_calls == [
        (overlay_failure_notice, "error"),
        (dashboard_module.t("dashboard.local_stt_notice_missing"), "warning"),
        (dashboard_module.t("dashboard.managed_auth_pending"), "info"),
        (dashboard_module.t("dashboard.local_stt_notice_missing"), "warning"),
        (overlay_failure_notice, "error"),
    ]


def test_dashboard_steamvr_overlay_failure_notice_uses_actionable_reason_without_status_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    expected_notice = dashboard_module.t(
        "settings.overlay.failure.steamvr_not_running",
        default="steamvr_not_running",
    )

    view.set_overlay_peer_contract(
        OverlayPeerConsumerContract(
            overlay=OverlayPeerToggleContract(
                intent_enabled=True,
                effective_enabled=False,
                action_enabled=True,
                state="warning",
                status_text="stale contract literal",
                failure_reason="steamvr_not_running",
            ),
            peer=OverlayPeerToggleContract(
                intent_enabled=False,
                effective_enabled=False,
                action_enabled=True,
                state="off",
                status_text="Off",
            ),
        )
    )

    assert view.display_card.notice_calls[-1] == (expected_notice, "error")


def test_dashboard_overlay_failure_notice_relocalizes_on_apply_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    view.set_overlay_peer_contract(
        OverlayPeerConsumerContract(
            overlay=OverlayPeerToggleContract(
                intent_enabled=True,
                effective_enabled=False,
                action_enabled=True,
                state="warning",
                status_text="stale contract literal",
                failure_reason="runtime_disconnected",
            ),
            peer=OverlayPeerToggleContract(
                intent_enabled=False,
                effective_enabled=False,
                action_enabled=True,
                state="off",
                status_text="Off",
            ),
        )
    )

    def localized_t(key: str, **kwargs: object) -> str:
        if key == "settings.overlay.status.failed":
            return "localized failed"
        if key == "settings.overlay.failure.runtime_disconnected":
            return "localized disconnect"
        if key == "settings.overlay.status.failed_with_reason":
            return f"{kwargs['status']} :: {kwargs['reason']}"
        return f"i18n:{key}"

    monkeypatch.setattr(dashboard_module, "t", localized_t)

    view.apply_locale()

    assert view.display_card.notice_calls[-1] == (
        "localized failed :: localized disconnect",
        "error",
    )
    assert view.display_card.notice_calls[-1][0] != "stale contract literal"


def test_dashboard_overlay_and_peer_buttons_toggle_live_from_contract_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    peer_toggles: list[bool] = []
    overlay_toggles: list[bool] = []
    view.on_toggle_peer_translation = lambda enabled: peer_toggles.append(enabled)
    view.on_toggle_overlay = lambda enabled: overlay_toggles.append(enabled)

    view.set_overlay_peer_contract(
        _make_overlay_peer_contract(
            overlay_intent_enabled=False,
            overlay_state="off",
            overlay_status_text="Overlay off",
            peer_intent_enabled=False,
            peer_effective_enabled=False,
            peer_status_text="Peer off",
        )
    )
    view.peer_button.on_click(None)
    view.overlay_button.on_click(None)

    view.set_overlay_peer_contract(
        _make_overlay_peer_contract(
            overlay_intent_enabled=True,
            overlay_state="connected",
            overlay_status_text="Overlay on",
            peer_intent_enabled=True,
            peer_effective_enabled=True,
            peer_status_text="Peer on",
        )
    )
    # The overlay toggle debounces double-fires within 0.4s of wall clock;
    # reset the stamp so the second scripted click is not swallowed.
    view._last_overlay_toggle_ts = 0.0
    view.peer_button.on_click(None)
    view.overlay_button.on_click(None)

    assert peer_toggles == [True, False]
    assert overlay_toggles == [True, False]


def test_dashboard_peer_source_selection_pins_language_and_mirrors_typed_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple] = []
    view.on_language_change = lambda *args: changes.append(args)
    view.set_languages_from_codes("ko", "en", "ja", "fr")

    view._on_peer_source_select("de")

    assert view._peer_source_lang_code == "de"
    assert view._peer_target_lang_code == "fr"
    # Unified view: the typed-output target mirrors the peer's language.
    assert view._target_lang_code == "de"
    assert view._recent_source_langs == ["de"]
    assert changes[-1] == ("ko", "de", "de", "fr", 0, [], [])
    assert _card_text(view._peer_src_card) == "name-de"


def test_dashboard_peer_target_selection_restores_follow_self_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple] = []
    view.on_language_change = lambda *args: changes.append(args)
    view.set_languages_from_codes("ko", "en", "ja", "fr")

    # Picking your own spoken language as the peer's target collapses the
    # explicit override back to "follow my language" (blank).
    view._on_peer_target_select("ko")

    assert view._peer_source_lang_code == "ja"
    assert view._peer_target_lang_code == ""
    assert view._effective_peer_target_lang_code() == "ko"
    assert view._recent_target_langs == ["ko"]
    assert changes[-1] == ("ko", "ja", "ja", "", 0, [], [])
    assert _card_text(view._peer_tgt_card) == "name-ko"


def test_dashboard_self_source_change_preserves_explicit_peer_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple] = []
    view.on_language_change = lambda *args: changes.append(args)
    view.set_languages_from_codes("ko", "en", "ja", "fr")

    view._on_source_select("ja")
    view._on_source_select("de")

    assert view._peer_source_lang_code == "ja"
    assert view._peer_target_lang_code == "fr"
    assert changes[-2] == ("ja", "ja", "ja", "fr", 0, [], [])
    assert changes[-1] == ("de", "ja", "ja", "fr", 0, [], [])
    assert _card_text(view._src_lang_card) == "name-de"


def test_dashboard_peer_language_edits_share_controller_update_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple] = []
    view.on_language_change = lambda *args: changes.append(args)

    view._on_peer_source_select("ja")
    view._on_peer_target_select("fr")

    assert changes == [
        ("ko", "ja", "ja", "", 0, [], []),
        ("ko", "ja", "ja", "fr", 0, [], []),
    ]


def test_dashboard_peer_swap_exchanges_source_and_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple] = []
    view.on_language_change = lambda *args: changes.append(args)
    view.set_languages_from_codes("ko", "en", "ja", "fr")

    view._swap_peer_languages()

    assert view._peer_source_lang_code == "fr"
    assert view._peer_target_lang_code == "ja"
    assert changes[-1] == ("ko", "ja", "fr", "ja", 0, [], [])
    assert _card_text(view._peer_src_card) == "name-fr"
    assert _card_text(view._peer_tgt_card) == "name-ja"


def test_dashboard_self_and_peer_language_row_labels_render_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_module, "t", lambda key, **_kwargs: f"i18n:{key}")
    view = _make_dashboard(monkeypatch)

    assert view._lbl_you_speak.value == "i18n:dashboard.you_speak"
    assert view._lbl_peer_voice.value == "i18n:dashboard.language.peer"

    view.apply_locale()

    assert view._lbl_you_speak.value == "i18n:dashboard.you_speak"
    assert view._lbl_peer_voice.value == "i18n:dashboard.language.peer"


def test_dashboard_peer_and_overlay_button_labels_render_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_module, "t", lambda key, **_kwargs: f"i18n:{key}")
    view = _make_dashboard(monkeypatch)

    assert _row_label(view.peer_button) == "i18n:dashboard.peer_label"
    assert _row_label(view.overlay_button) == "i18n:dashboard.overlay_label"

    view.apply_locale()

    assert _row_label(view.peer_button) == "i18n:dashboard.peer_label"
    assert _row_label(view.overlay_button) == "i18n:dashboard.overlay_label"


def test_dashboard_local_stt_notice_can_change_and_clear_without_touching_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)

    view.set_local_stt_notice("missing")
    view.set_display_text("hello", language_code="ko")
    view.set_local_stt_notice("downloading", percent=63)
    view.set_local_stt_notice(None)

    assert view.display_card.display_calls == [("hello", False, "font-ko")]
    assert view.display_card.notice_calls == [
        (dashboard_module.t("dashboard.local_stt_notice_missing"), "warning"),
        (dashboard_module.t("dashboard.local_stt_notice_downloading_progress", percent=63), "info"),
        (None, None),
    ]
