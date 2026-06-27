import asyncio
import contextlib
import copy
import inspect
import logging
import tempfile
import webbrowser
from pathlib import Path

import flet as ft

from puripuly_heart.config.llm_profiles import (
    get_openrouter_selection_alias_for_model_and_source,
    profile_for_alias,
)
from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterLLMModel,
    OpenRouterProviderRouting,
    OpenRouterSelectionAlias,
    TranslationConnection,
    effective_show_peer_original,
    save_settings,
)
from puripuly_heart.core.discord_oauth_loopback import (
    render_discord_oauth_callback_completion_page,
)
from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.core.managed_openrouter_release import TalkTogetherPassStatus
from puripuly_heart.core.updater import check_for_update
from puripuly_heart.ui.components.debug_preview_panel import DebugPreviewPanel
from puripuly_heart.ui.components.discord_managed_auth_dialog import DiscordManagedAuthDialog
from puripuly_heart.ui.components.founder_letter_dialog import FounderLetterDialog
from puripuly_heart.ui.components.local_qwen_hallucination_dialog import (
    LocalQwenHallucinationDialog,
)
from puripuly_heart.ui.components.microphone_test_dialog import MicrophoneTestDialog
from puripuly_heart.ui.components.peer_translation_eula_dialog import PeerTranslationEulaDialog
from puripuly_heart.ui.controller import GuiController
from puripuly_heart.ui.fonts import font_for_language, register_fonts
from puripuly_heart.ui.i18n import (
    get_locale,
    language_name,
    t,
)
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    get_app_theme,
)
from puripuly_heart.ui.views.about import AboutView
from puripuly_heart.ui.views.dashboard import DashboardView
from puripuly_heart.ui.views.logs import LogsView
from puripuly_heart.ui.views.settings import SettingsView

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_WIDTH = 860
DEFAULT_WINDOW_HEIGHT = 680
MIN_WINDOW_WIDTH = 720
MIN_WINDOW_HEIGHT = 560
APP_CONTENT_PADDING = 16
FOUNDER_CONTACT_URL = "https://x.com/kapitalismho"
FOUNDER_README_BASE_URL = "https://github.com/kapitalismho/PuriPuly-heart/blob/main"
FOUNDER_README_PATH_BY_LOCALE = {
    "ko": "README.ko.md",
    "zh-CN": "README.zh-CN.md",
    "ja": "README.ja.md",
}
FOUNDER_README_API_KEYS_ANCHOR_BY_LOCALE = {
    "ko": "자신의-api-키-사용하기",
    "zh-CN": "使用您自己的-api-密钥",
    "ja": "自分のapiキーを使う",
}
FOUNDER_README_DEFAULT_API_KEYS_ANCHOR = "using-your-own-api-keys"
DEBUG_PREVIEW_TALK_TOGETHER_PASS_ID = "7KQ9M2"
GITHUB_STAR_REPOSITORY_URL = "https://github.com/kapitalismho/PuriPuly-heart"
GITHUB_STAR_PROMPT_DELAY_S = 2.5
GITHUB_STAR_PROMPT_DURATION_MS = 8000


def _callable_accepts_keyword(callable_obj: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return True
    return keyword in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def founder_readme_url_for_locale(locale: str | None) -> str:
    readme_path = FOUNDER_README_PATH_BY_LOCALE.get(locale or "", "README.md")
    anchor = FOUNDER_README_API_KEYS_ANCHOR_BY_LOCALE.get(
        locale or "", FOUNDER_README_DEFAULT_API_KEYS_ANCHOR
    )
    return f"{FOUNDER_README_BASE_URL}/{readme_path}#{anchor}"


def _write_discord_callback_preview_page(locale: str | None) -> str:
    html = render_discord_oauth_callback_completion_page(locale)
    with tempfile.NamedTemporaryFile(
        "wb",
        prefix="puripuly-discord-callback-",
        suffix=".html",
        delete=False,
    ) as handle:
        handle.write(html)
        path = Path(handle.name)
    return path.as_uri()


class TranslatorApp:
    def __init__(self, page: ft.Page, *, config_path, debug_ui_preview: bool = False):
        self.page = page
        self.controller = GuiController(
            page=page,
            app=self,
            config_path=config_path,
        )
        self.overlay_state = "off"
        self.overlay_failure_reason: str | None = None
        self.overlay_peer_contract = None
        self.debug_ui_preview = bool(debug_ui_preview)
        self.debug_preview_panel: DebugPreviewPanel | None = None
        self._openrouter_pkce_request_active = False
        self._discord_managed_auth_generation = 0
        self._discord_managed_auth_cancelled = False
        self._discord_managed_auth_task_handle = None
        self._github_star_prompt_launch_pending = True
        self._launch_high_priority_feedback_shown = False
        self._launch_high_priority_feedback_reason: str | None = None
        self._launch_high_priority_snackbar = None
        self._github_star_prompt_shown_this_launch = False
        self._microphone_test_dialog: MicrophoneTestDialog | None = None
        self._setup_page()
        self._build_layout()

        # Link Dashboard callbacks
        self.view_dashboard.on_send_message = self._on_manual_submit
        self.view_dashboard.on_toggle_translation = self._on_translation_toggle
        self.view_dashboard.on_toggle_stt = self._on_stt_toggle
        self.view_dashboard.on_toggle_overlay = self._on_overlay_toggle
        self.view_dashboard.on_toggle_peer_translation = self._on_peer_translation_toggle
        self.view_dashboard.on_language_change = self._on_language_change
        self.view_dashboard.on_filter_peer_by_target_languages_change = self._on_filter_peer_change
        self.view_dashboard.on_translator_change = self._on_translator_change
        self.view_dashboard.on_transliteration_change = self._on_transliteration_change
        self.view_dashboard.on_pinyin_word_grouping_change = self._on_pinyin_word_grouping_change
        self.view_dashboard.on_chatbox_format_change = self._on_chatbox_format_change
        self.view_dashboard.on_request_current_translator = self._current_translator_model_value
        self.view_dashboard.on_request_deepl_usage_refresh = self._on_request_deepl_usage_refresh
        self.view_dashboard.on_request_stt_download = self._on_request_stt_download
        self.view_dashboard.on_stt_provider_change = self._on_dashboard_stt_provider_change
        self.view_dashboard.on_peer_stt_provider_change = self._on_dashboard_peer_stt_provider_change
        self.view_dashboard.on_overlay_lock_change = self._on_dashboard_overlay_lock_change
        self.view_dashboard.on_overlay_transparency_change = self._on_overlay_transparency_change
        self.view_dashboard.on_chatbox_send_peer_toggle = self._on_dashboard_chatbox_send_peer_toggle
        self.view_dashboard.on_loopback_mode_change = self._on_dashboard_loopback_mode_change
        self.view_dashboard.on_loopback_translation_only_change = (
            self._on_dashboard_loopback_translation_only_change
        )
        self.view_dashboard.on_self_in_overlay_toggle = self._on_dashboard_self_in_overlay_toggle
        self.view_dashboard.on_typed_in_overlay_toggle = self._on_dashboard_typed_in_overlay_toggle
        self.view_dashboard.on_vrc_mute_sync_toggle = self._on_dashboard_vrc_mute_sync_toggle
        self.view_dashboard.on_overlay_mode_select = self._on_dashboard_overlay_mode_select
        self.view_dashboard.on_overlay_single_turn_change = self._on_dashboard_overlay_single_turn_change
        self.view_dashboard.on_overlay_display_toggle = self._on_dashboard_overlay_display_toggle
        self.view_dashboard.on_overlay_size_select = self._on_dashboard_overlay_size_change

        self.view_settings.on_settings_changed = self._on_settings_changed
        self.view_settings.on_prompt_apply_settings = self._on_prompt_apply_settings
        self.view_settings.on_providers_changed = self._on_providers_changed
        self.view_settings.on_request_openrouter_pkce = self._on_request_openrouter_pkce
        self.view_settings.on_verify_api_key = self._on_verify_api_key
        self.view_settings.on_secret_cleared = self._on_secret_cleared
        self.view_settings.on_local_llm_secret_changed = self._on_local_llm_secret_changed
        self.view_settings.on_start_microphone_test = self._on_start_microphone_test
        self.view_settings.on_desktop_overlay_lock_change = self._on_desktop_overlay_lock_change
        self.view_settings.on_desktop_overlay_size_change = self._on_desktop_overlay_size_change
        self.view_settings.on_desktop_overlay_recovery_action = (
            self._on_desktop_overlay_recovery_action
        )
        self.view_settings.on_desktop_overlay_position_reset = (
            self._on_desktop_overlay_position_reset
        )
        self.view_settings.on_view_logs = self._open_logs_tab
        self.view_settings.show_snackbar = self._show_snackbar
        self.view_logs.on_mode_change = self._on_runtime_logging_mode_change
        self.view_logs.set_runtime_logging_mode(self.controller.runtime_logging_mode)
        runtime_log_basic = getattr(self.controller, "log_basic", None)
        runtime_log_detailed = getattr(self.controller, "log_detailed", None)
        if callable(runtime_log_basic):
            self.view_settings.runtime_log_basic = runtime_log_basic
        if callable(runtime_log_detailed):
            self.view_settings.runtime_log_detailed = runtime_log_detailed
        self.view_dashboard.runtime_log_detailed = self._log_detailed

        calibration_begin = getattr(self.controller, "begin_overlay_calibration", None)
        calibration_change = getattr(self.controller, "set_overlay_calibration_field", None)
        calibration_apply = getattr(self.controller, "apply_overlay_calibration", None)
        calibration_cancel = getattr(self.controller, "cancel_overlay_calibration", None)
        if callable(calibration_begin):
            self.view_settings.on_overlay_calibration_begin = calibration_begin
        if callable(calibration_change):
            self.view_settings.on_overlay_calibration_change = calibration_change
        if callable(calibration_apply):
            self.view_settings.on_overlay_calibration_apply = calibration_apply
        if callable(calibration_cancel):
            self.view_settings.on_overlay_calibration_cancel = calibration_cancel

        set_overlay_calibration = getattr(self.view_settings, "set_overlay_calibration", None)
        overlay_calibration = getattr(self.controller, "overlay_calibration", None)
        if callable(set_overlay_calibration) and overlay_calibration is not None:
            set_overlay_calibration(overlay_calibration)

    def _setup_page(self):
        self.page.title = t("app.title")
        self.page.theme_mode = ft.ThemeMode.DARK
        register_fonts(self.page)
        self.page.theme = get_app_theme(font_family=font_for_language(get_locale()))
        self.page.bgcolor = "#2e2f32"
        self.page.padding = 0
        self.page.window.frameless = False
        self.page.window.resizable = True
        self.page.window.width = DEFAULT_WINDOW_WIDTH
        self.page.window.height = DEFAULT_WINDOW_HEIGHT
        self.page.window.min_width = MIN_WINDOW_WIDTH
        self.page.window.min_height = MIN_WINDOW_HEIGHT
        self.page.window.icon = "icons/icon.ico"
        self.page.on_keyboard_event = self._on_keyboard_event
        self.page.on_resize = self._on_page_resize

    def _build_layout(self):
        self.view_dashboard = DashboardView()
        self.view_settings = SettingsView()
        self.view_logs = LogsView()
        self.view_about = AboutView()
        self.view_settings.set_overlay_runtime_state(self.overlay_state)

        self._nav_selected = 0
        # Wire dashboard sidebar nav → app navigation
        self.view_dashboard.on_nav_change = self._on_nav_change

        # Top nav bar for non-dashboard views (back + tab icons)
        _NAV_ICONS = [
            (ft.Icons.SETTINGS, "Settings"),
            (ft.Icons.ARTICLE, "Logs"),
            (ft.Icons.INFO_OUTLINE, "About"),
        ]
        _ON = "#48a495"
        _OFF = "#6e7175"
        self._top_nav_icons: list[ft.Icon] = []
        top_nav_tabs: list[ft.Control] = []
        _NAV_INDEX_OFFSET = 1  # Dashboard (0) removed from top nav; icons map to views 1,2,3
        for i, (icon_name, _label) in enumerate(_NAV_ICONS):
            app_idx = i + _NAV_INDEX_OFFSET
            ic = ft.Icon(icon_name, size=18, color=_OFF)
            self._top_nav_icons.append(ic)
            top_nav_tabs.append(
                ft.Container(
                    content=ic,
                    width=40,
                    height=36,
                    alignment=ft.alignment.center,
                    border_radius=6,
                    bgcolor=ft.Colors.TRANSPARENT,
                    on_click=lambda _, idx=app_idx: self._on_nav_change(idx),
                )
            )
        self._top_nav_bar = ft.Container(
            content=ft.Row(
                [
                    ft.IconButton(
                        ft.Icons.ARROW_BACK,
                        icon_size=18,
                        icon_color=_OFF,
                        on_click=lambda _: self._on_nav_change(0),
                        style=ft.ButtonStyle(
                            padding=ft.padding.all(6),
                            overlay_color=ft.Colors.TRANSPARENT,
                        ),
                    ),
                    ft.Container(width=8),
                    *top_nav_tabs,
                ],
                spacing=2,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor="#3a3b3e",
            height=44,
            padding=ft.padding.symmetric(horizontal=8),
            border=ft.border.only(bottom=ft.BorderSide(1, "#3f4044")),
            visible=False,
        )

        # Content area (full width — nav icons live in the dashboard sidebar)
        self._inner_content = ft.Container(expand=True, padding=0, content=self.view_dashboard)
        self.content_area = ft.Container(
            expand=True,
            padding=0,
            content=ft.Column(
                [self._top_nav_bar, self._inner_content],
                spacing=0,
                expand=True,
            ),
        )

        self.layout = ft.Row(
            controls=[self.content_area],
            expand=True,
            spacing=0,
        )

        root_content = ft.Container(content=self.layout, expand=True, padding=0)
        if self.debug_ui_preview:
            self.debug_preview_panel = self._build_debug_preview_panel()
            self.page.add(
                ft.Container(
                    content=ft.Stack(
                        controls=[root_content, self.debug_preview_panel],
                        fit=ft.StackFit.EXPAND,
                        expand=True,
                    ),
                    expand=True,
                    padding=0,
                )
            )
        else:
            self.page.add(root_content)

    def _build_debug_preview_panel(self) -> DebugPreviewPanel:
        return DebugPreviewPanel(
            on_brake_notice=self._preview_brake_notice,
            on_revoked_notice=self._preview_revoked_notice,
            on_founder_letter=self._preview_founder_letter,
            on_pkce_failure=self._preview_pkce_failure,
            on_discord_auth=self._preview_discord_auth,
            on_discord_callback_page=self._preview_discord_callback_page,
            on_peer_translation_eula=self._preview_peer_translation_eula,
            on_local_qwen_hallucination_modal=self._preview_local_qwen_hallucination_modal,
            on_talk_together_pass_invite_progress=(
                self._preview_talk_together_pass_invite_progress
            ),
            on_capture_fault_cycle=self._preview_capture_fault_cycle,
            on_stt_fault_cycle=self._preview_stt_fault_cycle,
            on_audio_fault_clear=self._preview_audio_fault_clear,
            on_github_star_snackbar=self._preview_github_star_snackbar,
        )

    def _mark_launch_high_priority_feedback_shown(
        self,
        reason: str,
        snackbar: object | None = None,
    ) -> None:
        if not getattr(self, "_github_star_prompt_launch_pending", True):
            return
        self._launch_high_priority_feedback_shown = True
        self._launch_high_priority_feedback_reason = reason
        if snackbar is not None:
            self._launch_high_priority_snackbar = snackbar

    def _launch_feedback_conflicts_with_github_star_prompt(self) -> bool:
        if getattr(self, "_launch_high_priority_feedback_shown", False):
            return True
        snackbar = getattr(self, "_launch_high_priority_snackbar", None)
        return bool(getattr(snackbar, "open", False))

    async def maybe_show_github_star_prompt_after_launch(
        self,
        *,
        delay_s: float = GITHUB_STAR_PROMPT_DELAY_S,
    ) -> bool:
        try:
            controller = getattr(self, "controller", None)
            persist_eligible_launch = getattr(
                controller,
                "persist_github_star_prompt_eligible_launch",
                None,
            )
            if not callable(persist_eligible_launch):
                return False
            launch_gate_satisfied = await persist_eligible_launch()
            if self._launch_feedback_conflicts_with_github_star_prompt():
                return False
            if not launch_gate_satisfied:
                return False
            should_show = getattr(controller, "should_show_github_star_prompt", None)
            if not callable(should_show) or not should_show():
                return False

            await asyncio.sleep(delay_s)

            if self._launch_feedback_conflicts_with_github_star_prompt():
                return False
            if not should_show():
                return False
            return await self._open_github_star_prompt_snackbar(
                should_open=lambda: not self._launch_feedback_conflicts_with_github_star_prompt()
            )
        finally:
            self._github_star_prompt_launch_pending = False

    async def _open_github_star_prompt_snackbar(self, *, should_open=None) -> bool:  # noqa: ANN001
        if getattr(self, "_github_star_prompt_shown_this_launch", False):
            return False
        controller = getattr(self, "controller", None)
        persist_opened = getattr(controller, "persist_github_star_prompt_opened", None)
        if not callable(persist_opened) or not await persist_opened(should_open=should_open):
            return False

        snackbar = None

        def _open_repository(_event) -> None:  # noqa: ANN001
            async def _persist_click() -> None:
                persist_clicked = getattr(controller, "persist_github_star_prompt_clicked", None)
                if callable(persist_clicked):
                    await persist_clicked()

            self._queue_settings_mutation_task(_persist_click)
            webbrowser.open(GITHUB_STAR_REPOSITORY_URL)
            if snackbar is not None:
                self._close_github_star_prompt_snackbar(snackbar)

        snackbar = self._build_github_star_prompt_snackbar(_open_repository)
        self._github_star_prompt_shown_this_launch = True
        self.page.open(snackbar)
        return True

    def _build_github_star_prompt_snackbar(self, on_click) -> ft.SnackBar:  # noqa: ANN001
        return ft.SnackBar(
            content=ft.Row(
                controls=[
                    ft.Text(
                        t("github_star.snackbar.message"),
                        size=18,
                        color=ft.Colors.WHITE,
                        font_family=font_for_language(get_locale()),
                        expand=True,
                    ),
                    ft.TextButton(
                        text=t("github_star.snackbar.action"),
                        on_click=on_click,
                        style=ft.ButtonStyle(
                            color=ft.Colors.WHITE,
                            text_style=ft.TextStyle(
                                size=18,
                                font_family=font_for_language(get_locale()),
                            ),
                            overlay_color=COLOR_PRIMARY,
                        ),
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            bgcolor=COLOR_SUCCESS,
            duration=GITHUB_STAR_PROMPT_DURATION_MS,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.margin.only(bottom=90),
            padding=20,
        )

    def _close_github_star_prompt_snackbar(self, snackbar: ft.SnackBar) -> None:
        close = getattr(self.page, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close(snackbar)
        else:
            snackbar.open = False
            with contextlib.suppress(Exception):
                self.page.update()
        self._displace_current_snackbar_for_flet_028()

    def _displace_current_snackbar_for_flet_028(self) -> None:
        """Force-dismiss the visible SnackBar on Flet 0.28.x.

        Flet 0.28.3 updates the Python-side ``SnackBar.open`` flag on
        ``page.close(snackbar)`` but the Flutter-side snackbar remains visible
        until its duration expires. Opening another SnackBar first removes the
        current one, so use a transparent 1 ms replacement as a narrow shim.
        """

        open_control = getattr(self.page, "open", None)
        if not callable(open_control):
            return
        dismissor = ft.SnackBar(
            content=ft.Text("", size=0),
            bgcolor=ft.Colors.TRANSPARENT,
            duration=1,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.margin.only(bottom=90),
            padding=0,
        )
        with contextlib.suppress(Exception):
            open_control(dismissor)

    def _preview_github_star_snackbar(self) -> None:
        snackbar = None

        def _open_repository(_event) -> None:  # noqa: ANN001
            webbrowser.open(GITHUB_STAR_REPOSITORY_URL)
            if snackbar is not None:
                self._close_github_star_prompt_snackbar(snackbar)

        snackbar = self._build_github_star_prompt_snackbar(_open_repository)
        self.page.open(snackbar)

    def _preview_brake_notice(self) -> None:
        self._show_snackbar(t("managed_release.brake"), ft.Colors.ORANGE_700)

    def _preview_revoked_notice(self) -> None:
        self._show_snackbar(t("managed_release.revoked_contact"), ft.Colors.ORANGE_700)

    def _debug_preview_noop(self) -> None:
        return None

    def _preview_founder_letter(self) -> None:
        dialog = FounderLetterDialog(self.page, on_readme=self._on_founder_letter_readme)
        self._founder_letter_dialog = dialog
        dialog.open()

    def _preview_pkce_failure(self) -> None:
        self._show_snackbar(t("openrouter.pkce.failed"), ft.Colors.ORANGE_700)

    def _preview_discord_auth(self) -> None:
        self.show_discord_managed_auth_dialog(preview=True)

    def _preview_discord_callback_page(self) -> None:
        webbrowser.open(_write_discord_callback_preview_page(get_locale()))

    def _preview_peer_translation_eula(self) -> None:
        self._show_peer_translation_eula(self._debug_preview_noop)

    def _preview_local_qwen_hallucination_modal(self) -> None:
        self.show_local_qwen_hallucination_dialog()

    def _preview_talk_together_pass_invite_progress(self) -> None:
        set_managed_key_state = getattr(self.view_settings, "set_managed_key_state", None)
        if not callable(set_managed_key_state):
            return
        set_managed_key_state(
            visible=True,
            remaining_percent=100,
            referral_id=DEBUG_PREVIEW_TALK_TOGETHER_PASS_ID,
            remember_referral_id=False,
            pass_status=TalkTogetherPassStatus(
                pass_id=DEBUG_PREVIEW_TALK_TOGETHER_PASS_ID,
                invite_count=1,
                invite_limit=5,
                bonus_translations_per_friend=200,
            ),
        )

    def _preview_capture_fault_cycle(self) -> None:
        profile = self.controller.cycle_debug_capture_fault_profile()
        self._show_snackbar(
            t("debug_preview.capture_fault_snackbar", profile=profile), ft.Colors.ORANGE_700
        )

    def _preview_stt_fault_cycle(self) -> None:
        profile = self.controller.cycle_debug_stt_fault_profile()
        self._show_snackbar(
            t("debug_preview.stt_fault_snackbar", profile=profile), ft.Colors.ORANGE_700
        )

    def _preview_audio_fault_clear(self) -> None:
        self.controller.clear_debug_audio_fault_profiles()
        self._show_snackbar(t("debug_preview.audio_fault_clear"), ft.Colors.GREEN_700)

    def _show_peer_translation_eula(self, on_accept) -> None:
        dialog = PeerTranslationEulaDialog(
            self.page,
            on_accept=on_accept,
            on_cancel=self._debug_preview_noop,
        )
        self._peer_translation_eula_dialog = dialog
        dialog.open()

    def show_local_qwen_hallucination_dialog(self) -> None:
        dialog = LocalQwenHallucinationDialog(
            self.page,
            on_open_guide=self._open_local_qwen_guide,
        )
        self._local_qwen_hallucination_dialog = dialog
        dialog.open()

    def _open_local_qwen_guide(self) -> None:
        webbrowser.open(founder_readme_url_for_locale(get_locale()))

    def _accept_peer_translation_eula_and_enable(self) -> None:
        async def _task():
            settings = getattr(self.controller, "settings", None)
            if settings is not None:
                settings.ui.peer_translation_eula_accepted = True
                config_path = getattr(self.controller, "config_path", None)
                if config_path is not None:
                    save_settings(config_path, settings)
            await self.controller.set_peer_translation_enabled(True)

        self.page.run_task(_task)

    def _close_open_dialog_for_navigation(self) -> None:
        microphone_test_dialog = getattr(self, "_microphone_test_dialog", None)
        if microphone_test_dialog is not None and getattr(
            microphone_test_dialog,
            "is_open",
            False,
        ):
            microphone_test_dialog.close(notify=True)
            return

        dialog = getattr(self.page, "dialog", None)
        close_dialog = getattr(self.page, "close", None)
        if dialog is None or not callable(close_dialog):
            return
        try:
            close_dialog(dialog)
        except Exception:
            logger.exception("Failed to close dialog during navigation")

    def _queue_settings_mutation_task(self, task_factory) -> None:
        queue = getattr(self, "_settings_mutation_queue", None)
        if queue is None:
            queue = []
            self._settings_mutation_queue = queue
        queue.append(task_factory)
        if getattr(self, "_settings_mutation_worker_active", False):
            return
        self._settings_mutation_worker_active = True

        async def _worker():
            try:
                while self._settings_mutation_queue:
                    next_task = self._settings_mutation_queue.pop(0)
                    try:
                        await next_task()
                    except Exception:
                        logger.exception("Settings mutation task failed")
            finally:
                self._settings_mutation_worker_active = False

        self.page.run_task(_worker)

    def _content_padding_for_index(self, index: int) -> int:
        return 0 if index == 0 else APP_CONTENT_PADDING

    def _on_nav_change(self, index: int):
        # Track previous tab for Settings auto-apply
        previous_tab = getattr(self, "_current_tab", 0)
        if previous_tab != index:
            self._close_open_dialog_for_navigation()
        self._current_tab = index

        # Temporarily lock overlay while Settings is open so it doesn't block clicks
        dash = getattr(self, "view_dashboard", None)
        if index == 1 and previous_tab != 1:
            # Entering settings: remember lock state and lock
            if dash is not None:
                self._settings_tab_overlay_was_locked = getattr(dash, "_overlay_locked", False)
                if not self._settings_tab_overlay_was_locked:
                    try:
                        dash.set_overlay_locked(True)
                        if callable(getattr(dash, "on_overlay_lock_change", None)):
                            dash.on_overlay_lock_change(True)
                    except Exception:
                        pass
        elif previous_tab == 1 and index != 1:
            # Leaving settings: restore lock state
            was_locked = getattr(self, "_settings_tab_overlay_was_locked", None)
            if was_locked is not None and dash is not None:
                try:
                    dash.set_overlay_locked(bool(was_locked))
                    if callable(getattr(dash, "on_overlay_lock_change", None)):
                        dash.on_overlay_lock_change(bool(was_locked))
                except Exception:
                    pass

        # Auto-apply Settings changes when leaving Settings (tab 1)
        if previous_tab == 1 and index != 1:
            if self.view_settings.has_provider_changes:
                pending_settings = self.view_settings.consume_provider_apply_settings()
                if pending_settings is not None:
                    self.view_settings.has_provider_changes = False
                    self._sync_stt_label(pending_settings)
                    try:
                        sync_fn = getattr(self.view_settings, "sync_stt_provider_label", None)
                        if callable(sync_fn) and pending_settings is not None:
                            _prov = getattr(getattr(pending_settings, "provider", None), "stt", None)
                            if _prov is not None:
                                _val = _prov.value if hasattr(_prov, "value") else str(_prov)
                                sync_fn(_val)
                    except Exception:
                        pass

                    async def _task():
                        await self.controller.apply_providers(pending_settings)

                    self._queue_settings_mutation_task(_task)
            elif getattr(self.view_settings, "has_pending_prompt_changes", False):
                pending_settings = self.view_settings.consume_prompt_apply_settings()
                if pending_settings is not None:

                    async def _task():
                        merged_settings = (
                            self.controller.merge_settings_tab_apply_with_current_languages(
                                pending_settings
                            )
                        )
                        await self.controller.apply_settings(merged_settings)

                    self._queue_settings_mutation_task(_task)

        view_map = {0: self.view_dashboard, 1: self.view_settings, 2: self.view_logs, 3: self.view_about}
        self._inner_content.content = view_map.get(index, self.view_dashboard)
        self._inner_content.padding = self._content_padding_for_index(index)
        self._top_nav_bar.visible = index != 0
        for i, ic in enumerate(self._top_nav_icons):
            ic.color = "#48a495" if (i + 1) == index else "#6e7175"
        self.content_area.update()
        self._set_bottom_nav_selected(index)
        if index == 1:
            self.view_settings.refresh_prompt_if_empty()
        elif index == 2:
            # Async scroll after rendering completes
            async def _scroll():
                import asyncio

                await asyncio.sleep(0.05)
                await self.view_logs.scroll_to_bottom()

            self.page.run_task(_scroll)

    def _open_logs_tab(self) -> None:
        self._on_nav_change(2)
        self._set_bottom_nav_selected(2)

    def _open_settings_tab(self) -> None:
        self._on_nav_change(1)
        self._set_bottom_nav_selected(1)

    def _set_bottom_nav_selected(self, index: int) -> None:
        self._nav_selected = index
        with contextlib.suppress(Exception):
            self.view_dashboard.set_sidebar_nav_selected(index)

    def apply_locale(self) -> None:
        self.page.title = t("app.title")
        self.page.theme = get_app_theme(font_family=font_for_language(get_locale()))
        self.view_dashboard.apply_locale()
        self.view_settings.apply_locale()
        self.refresh_overlay_peer_contract()
        self.view_logs.apply_locale()
        debug_preview_panel = getattr(self, "debug_preview_panel", None)
        apply_debug_locale = getattr(debug_preview_panel, "apply_locale", None)
        if callable(apply_debug_locale):
            apply_debug_locale()
        self.page.update()

    def refresh_overlay_peer_contract(self) -> None:
        controller = getattr(self, "controller", None)
        build_contract = getattr(controller, "build_overlay_peer_consumer_contract", None)
        if not callable(build_contract):
            return
        contract = build_contract()
        self.overlay_peer_contract = contract
        if contract is None:
            return
        view_settings = getattr(self, "view_settings", None)
        set_settings_contract = getattr(view_settings, "set_overlay_peer_contract", None)
        if callable(set_settings_contract):
            set_settings_contract(contract)
        view_dashboard = getattr(self, "view_dashboard", None)
        set_dashboard_contract = getattr(view_dashboard, "set_overlay_peer_contract", None)
        if callable(set_dashboard_contract):
            set_dashboard_contract(contract)

    def _sync_settings_overlay_runtime_state(self) -> None:
        controller = getattr(self, "controller", None)
        settings = getattr(controller, "settings", None)
        overlay_target = None
        if settings is not None:
            overlay_target = getattr(settings.overlay, "target", None)
        # Prefer the actually-active target so the dashboard chip reflects the real
        # mode after SteamVR auto-fallback (e.g. shows PC when SteamVR is off even
        # though the stored preference is VR).
        if controller is not None:
            active_target = getattr(controller, "_active_overlay_target", None)
            if active_target:
                overlay_target = active_target
            else:
                resolve_effective = getattr(
                    controller, "_effective_overlay_target_for_launch", None
                )
                if callable(resolve_effective):
                    try:
                        overlay_target = resolve_effective(settings)
                    except Exception:
                        pass
        # Mirror the VR/Desktop mode onto the dashboard overlay button so users can
        # tell at a glance where captions render (avoids "why is it not showing?").
        # Done before the view_settings early-return so the chip is correct at
        # startup, not only after the first lock/overlay interaction.
        dash = getattr(self, "view_dashboard", None)
        set_mode = getattr(dash, "set_overlay_mode", None)
        if callable(set_mode):
            try:
                set_mode(overlay_target)
            except Exception:
                pass

        view_settings = getattr(self, "view_settings", None)
        set_state = getattr(view_settings, "set_overlay_runtime_state", None)
        if not callable(set_state):
            return
        desktop_locked = False
        if controller is not None:
            desktop_locked = bool(getattr(controller, "desktop_overlay_captions_locked", False))
        set_state(
            self.overlay_state,
            failure_reason=self.overlay_failure_reason,
            overlay_target=overlay_target,
            desktop_captions_locked=desktop_locked,
        )

    def _on_desktop_overlay_lock_change(self, locked: bool) -> None:
        async def _task():
            await self.controller.set_desktop_overlay_captions_locked(bool(locked))
            self._refresh_settings_desktop_overlay_state()

        self.page.run_task(_task)

    def _on_dashboard_overlay_mode_select(self, mode: str) -> None:
        import copy as _copy
        from puripuly_heart.config.settings import (
            OVERLAY_TARGET_DESKTOP,
            OVERLAY_TARGET_STEAMVR,
        )

        mode = str(mode).lower()
        live = getattr(self.controller, "settings", None)
        if live is None:
            return
        try:
            next_settings = _copy.deepcopy(live)
            overlay = next_settings.overlay
            if mode == "auto":
                overlay.auto_switch = True
            else:
                overlay.auto_switch = False
                overlay.target = (
                    OVERLAY_TARGET_DESKTOP
                    if mode == OVERLAY_TARGET_DESKTOP
                    else OVERLAY_TARGET_STEAMVR
                )
        except Exception:
            return
        # Keep the settings view's draft in sync so it can't overwrite this choice.
        try:
            sv = getattr(self, "view_settings", None)
            sv_settings = getattr(sv, "_settings", None)
            if sv_settings is not None and getattr(sv_settings, "overlay", None):
                sv_settings.overlay.auto_switch = next_settings.overlay.auto_switch
                sv_settings.overlay.target = next_settings.overlay.target
                if callable(getattr(sv, "_sync_overlay_controls", None)):
                    sv._sync_overlay_controls()
        except Exception:
            pass
        self._on_settings_changed(next_settings)

    def _on_dashboard_overlay_display_toggle(self, field: str, value: bool) -> None:
        import copy as _copy

        if field not in {"show_peer_original", "show_translation", "show_romanization"}:
            return
        live = getattr(self.controller, "settings", None)
        if live is None:
            return
        try:
            next_settings = _copy.deepcopy(live)
            setattr(next_settings.overlay, field, bool(value))
            if field == "show_peer_original":
                # Explicitly touching "orig" opts out of mirroring General's chatbox
                # format — from here on this device keeps its own explicit choice.
                next_settings.overlay.peer_original_follows_chatbox_format = False
        except Exception:
            return
        try:
            sv = getattr(self, "view_settings", None)
            sv_settings = getattr(sv, "_settings", None)
            if sv_settings is not None and getattr(sv_settings, "overlay", None):
                setattr(sv_settings.overlay, field, bool(value))
                if field == "show_peer_original":
                    sv_settings.overlay.peer_original_follows_chatbox_format = False
        except Exception:
            pass
        self._on_settings_changed(next_settings)

    def _on_dashboard_overlay_single_turn_change(self, value: bool) -> None:
        import copy as _copy

        live = getattr(self.controller, "settings", None)
        if live is None:
            return
        try:
            next_settings = _copy.deepcopy(live)
            next_settings.overlay.single_turn_mode = bool(value)
        except Exception:
            return
        try:
            sv = getattr(self, "view_settings", None)
            sv_settings = getattr(sv, "_settings", None)
            if sv_settings is not None and getattr(sv_settings, "overlay", None):
                sv_settings.overlay.single_turn_mode = bool(value)
        except Exception:
            pass
        self._on_settings_changed(next_settings)

    def _on_dashboard_overlay_lock_change(self, locked: bool) -> None:
        self._on_desktop_overlay_lock_change(locked)
        # Mirror locked state back to settings view button
        try:
            self.view_settings.set_desktop_captions_locked(locked)
        except Exception:
            pass

    def _on_dashboard_chatbox_send_peer_toggle(self, value: bool) -> None:
        _s = getattr(self.controller, "settings", None)
        if _s and getattr(_s, "ui", None):
            _s.ui.chatbox_send_peer = value
            try:
                from puripuly_heart.config.settings import save_settings
                save_settings(self.controller.config_path, _s)
            except Exception:
                pass
        # Keep settings view's internal draft in sync so it can't overwrite this toggle
        try:
            _sv_s = getattr(getattr(self, "view_settings", None), "_settings", None)
            if _sv_s and getattr(_sv_s, "ui", None):
                _sv_s.ui.chatbox_send_peer = value
        except Exception:
            pass
        if self.controller and self.controller.hub:
            self.controller.hub.chatbox_send_peer = value
        try:
            from puripuly_heart.ui.i18n import t
            self.view_settings._chatbox_send_peer_text.content.value = t(
                "settings.option.on" if value else "settings.option.off"
            )
            if self.view_settings.page:
                self.view_settings._chatbox_send_peer_text.update()
        except Exception:
            pass

    def _on_dashboard_loopback_mode_change(self, selected_only: bool) -> None:
        _s = getattr(self.controller, "settings", None)
        if _s and getattr(_s, "ui", None):
            _s.ui.loopback_selected_languages_only = selected_only
            try:
                from puripuly_heart.config.settings import save_settings
                save_settings(self.controller.config_path, _s)
            except Exception:
                pass
        try:
            _sv_s = getattr(getattr(self, "view_settings", None), "_settings", None)
            if _sv_s and getattr(_sv_s, "ui", None):
                _sv_s.ui.loopback_selected_languages_only = selected_only
        except Exception:
            pass
        if self.controller and self.controller.hub:
            self.controller.hub.loopback_selected_languages_only = selected_only

    def _on_dashboard_loopback_translation_only_change(self, translation_only: bool) -> None:
        _s = getattr(self.controller, "settings", None)
        if _s and getattr(_s, "ui", None):
            _s.ui.chatbox_send_peer_translation_only = translation_only
            try:
                from puripuly_heart.config.settings import save_settings
                save_settings(self.controller.config_path, _s)
            except Exception:
                pass
        try:
            _sv_s = getattr(getattr(self, "view_settings", None), "_settings", None)
            if _sv_s and getattr(_sv_s, "ui", None):
                _sv_s.ui.chatbox_send_peer_translation_only = translation_only
        except Exception:
            pass
        if self.controller and self.controller.hub:
            self.controller.hub.chatbox_send_peer_translation_only = translation_only

    def _on_dashboard_self_in_overlay_toggle(self, value: bool) -> None:
        _s = getattr(self.controller, "settings", None)
        if _s and getattr(_s, "ui", None):
            _s.ui.self_in_overlay = value
            try:
                from puripuly_heart.config.settings import save_settings
                save_settings(self.controller.config_path, _s)
            except Exception:
                pass
        if self.controller and self.controller.hub:
            self.controller.hub.self_in_overlay = value

    def _on_dashboard_typed_in_overlay_toggle(self, value: bool) -> None:
        _s = getattr(self.controller, "settings", None)
        if _s and getattr(_s, "ui", None):
            _s.ui.typed_in_overlay = value
            try:
                from puripuly_heart.config.settings import save_settings
                save_settings(self.controller.config_path, _s)
            except Exception:
                pass
        if self.controller and self.controller.hub:
            self.controller.hub.typed_in_overlay = value

    def _on_vrc_mute_osc_state_changed(self, muted: bool | None) -> None:
        """Callback from VrcMicState when VRChat sends a mute state update via OSC."""
        try:
            dash = getattr(self, "view_dashboard", None)
            if dash is not None and callable(getattr(dash, "set_vrc_mute_sync_osc_state", None)):
                dash.set_vrc_mute_sync_osc_state(muted)
        except Exception:
            pass

    def _on_dashboard_vrc_mute_sync_toggle(self, value: bool) -> None:
        _s = getattr(self.controller, "settings", None)
        if _s and getattr(_s, "osc", None):
            _s.osc.vrc_mic_intercept = value
            try:
                from puripuly_heart.config.settings import save_settings
                save_settings(self.controller.config_path, _s)
            except Exception:
                pass
        # Keep settings view's internal draft in sync so it can't overwrite this toggle
        try:
            _sv_s = getattr(getattr(self, "view_settings", None), "_settings", None)
            if _sv_s and getattr(_sv_s, "osc", None):
                _sv_s.osc.vrc_mic_intercept = value
        except Exception:
            pass

        async def _task():
            await self.controller._configure_vrc_mic_receiver(enabled=value)

        self.page.run_task(_task)

    def _on_overlay_transparency_change(self, alpha: float) -> None:
        async def _task():
            await self.controller.set_desktop_overlay_background_alpha(float(alpha))
            self._refresh_settings_desktop_overlay_state()

        self.page.run_task(_task)

    def _on_desktop_overlay_size_change(self, size_preset: str) -> None:
        async def _task():
            await self.controller.set_desktop_overlay_size_preset(size_preset)
            self._refresh_settings_desktop_overlay_state()

        self.page.run_task(_task)

    def _on_dashboard_overlay_size_change(self, size_preset: str) -> None:
        # Dashboard right-click "Size" submenu — same path as the Settings size
        # control so the two stay in sync (the controller persists the preset and
        # live-resizes the overlay; _refresh pushes the new value back to both views).
        async def _task():
            await self.controller.set_desktop_overlay_size_preset(size_preset)
            self._refresh_settings_desktop_overlay_state()

        self.page.run_task(_task)

    def _on_desktop_overlay_recovery_action(self, action: str) -> None:
        if action not in {"retry", "reopen"}:
            return

        async def _task():
            await self.controller.set_overlay_enabled(True)

        self.page.run_task(_task)

    def _on_desktop_overlay_position_reset(self) -> None:
        async def _task():
            await self.controller.reset_desktop_overlay_position()
            self._refresh_settings_desktop_overlay_state()

        self.page.run_task(_task)

    def _refresh_settings_desktop_overlay_state(self) -> None:
        controller = getattr(self, "controller", None)
        settings = getattr(controller, "settings", None)
        view_settings = getattr(self, "view_settings", None)
        sync_settings = getattr(view_settings, "sync_desktop_overlay_settings", None)
        if settings is not None and callable(sync_settings):
            sync_settings(settings)
        self._sync_settings_overlay_runtime_state()
        # Sync lock button + size submenu on dashboard
        try:
            locked = bool(getattr(controller, "desktop_overlay_captions_locked", False))
            dash = getattr(self, "view_dashboard", None)
            if dash is not None:
                set_locked = getattr(dash, "set_overlay_locked", None)
                if callable(set_locked):
                    set_locked(locked)
                set_size = getattr(dash, "set_overlay_size_preset", None)
                if callable(set_size) and settings is not None:
                    try:
                        preset = settings.overlay.desktop_flet.size_preset
                    except Exception:
                        preset = None
                    if preset:
                        set_size(preset)
        except Exception:
            pass

    def on_desktop_overlay_state_changed(
        self,
        *,
        interaction_mode: str | None = None,
        captions_locked: bool | None = None,
    ) -> None:
        # Mirror the overlay's own lock toggle (clicked from the in-overlay lock icon)
        # back onto the dashboard + settings lock controls so they stay in sync.
        if captions_locked is not None:
            dash = getattr(self, "view_dashboard", None)
            set_locked = getattr(dash, "set_overlay_locked", None)
            if callable(set_locked):
                try:
                    set_locked(bool(captions_locked))
                except Exception:
                    pass
            try:
                self.view_settings.set_desktop_captions_locked(bool(captions_locked))
            except Exception:
                pass
        self._sync_settings_overlay_runtime_state()

    def _on_manual_submit(self, _source: str, text: str) -> None:
        async def _task():
            await self.controller.submit_text(text)

        self.page.run_task(_task)

    def _on_page_resize(self, e) -> None:
        try:
            settings = getattr(self.controller, "settings", None)
            if settings is None:
                return
            w = int(self.page.window.width or 0)
            h = int(self.page.window.height or 0)
            if w >= MIN_WINDOW_WIDTH and h >= MIN_WINDOW_HEIGHT:
                settings.ui.window_width = w
                settings.ui.window_height = h
                save_settings(self.controller.config_path, settings)
        except Exception:
            pass

    def _on_keyboard_event(self, event) -> None:
        if getattr(event, "key", None) != "Tab":
            return
        if any(
            bool(getattr(event, modifier, False)) for modifier in ("shift", "ctrl", "alt", "meta")
        ):
            return

        dashboard = getattr(self, "view_dashboard", None)
        content_area = getattr(self, "content_area", None)
        if dashboard is None or getattr(content_area, "content", None) is not dashboard:
            return

        handler = getattr(dashboard, "handle_message_input_tab_key", None)
        if callable(handler):
            handler()

    def _log_basic(self, message: str, *, level: int = logging.INFO) -> None:
        controller = getattr(self, "controller", None)
        log_basic = getattr(controller, "log_basic", None)
        if callable(log_basic):
            log_basic(message, level=level)
            return
        logger.log(level, message)

    def _log_detailed(self, message: str, *, level: int = logging.INFO) -> None:
        controller = getattr(self, "controller", None)
        log_detailed = getattr(controller, "log_detailed", None)
        if callable(log_detailed):
            log_detailed(message, level=level)
            return
        logger.log(level, message)

    def _revert_dashboard_translation_toggle(self) -> None:
        self._set_dashboard_translation_visual_state(False)

    def _set_dashboard_translation_visual_state(self, enabled: bool) -> None:
        dash = getattr(self, "view_dashboard", None)
        set_translation_enabled = getattr(dash, "set_translation_enabled", None)
        if callable(set_translation_enabled):
            try:
                set_translation_enabled(enabled)
            except Exception:
                logger.exception("Failed to update dashboard translation toggle")

    def _dashboard_managed_auth_action(self) -> str:
        action = getattr(self.controller, "dashboard_managed_auth_action", None)
        if not callable(action):
            return "continue"
        try:
            return str(action())
        except Exception:
            logger.exception("Failed to evaluate managed auth dashboard gate")
            return "prompt"

    def _on_translation_toggle(self, enabled: bool) -> bool:
        self._log_basic(f"[Dashboard] Translation toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Translation toggle detail: "
            f"dashboard_state={getattr(getattr(self, 'view_dashboard', None), 'is_translation_on', None)} "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )
        if enabled:
            managed_auth_action = self._dashboard_managed_auth_action()
            if managed_auth_action in {"prompt", "in_progress"}:
                self._revert_dashboard_translation_toggle()
                if managed_auth_action == "prompt":
                    self.show_discord_managed_auth_dialog(preview=False)
                return False

        async def _task():
            await self.controller.set_translation_enabled(enabled)

        self.page.run_task(_task)
        return True

    def _on_stt_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] STT toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] STT toggle detail: "
            f"dashboard_state={getattr(getattr(self, 'view_dashboard', None), 'is_stt_on', None)} "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )

        async def _task():
            await self.controller.set_stt_enabled(enabled)

        self.page.run_task(_task)

    def _on_overlay_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] Overlay toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Overlay toggle detail: "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')} "
            f"failure_reason={getattr(self, 'overlay_failure_reason', None)}"
        )

        async def _task():
            await self.controller.set_overlay_enabled(enabled)

        self.page.run_task(_task)

    def _on_peer_translation_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] Peer toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Peer toggle detail: "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')} "
            f"failure_reason={getattr(self, 'overlay_failure_reason', None)}"
        )

        controller = getattr(self, "controller", None)
        settings = getattr(controller, "settings", None)
        ui_settings = getattr(settings, "ui", None)
        if (
            enabled
            and ui_settings is not None
            and not getattr(ui_settings, "peer_translation_eula_accepted", False)
        ):
            self._show_peer_translation_eula(self._accept_peer_translation_eula_and_enable)
            return

        async def _task():
            await self.controller.set_peer_translation_enabled(enabled)

        self.page.run_task(_task)

    def _on_language_change(
        self,
        source_code: str,
        target_code: str,
        peer_source_code: str = "",
        peer_target_code: str = "",
        preset_index: int | None = None,
        extra_target_codes: list[str] | None = None,
        extra_peer_source_codes: list[str] | None = None,
    ) -> None:
        if self.controller.settings is None:
            return
        settings = self.controller.settings
        previous_source_code = settings.languages.source_language
        previous_target_code = settings.languages.target_language
        previous_peer_source_code = getattr(settings.languages, "peer_source_language", "")
        previous_peer_target_code = getattr(settings.languages, "peer_target_language", "")
        self._log_basic(
            "[Dashboard] Language change requested: "
            f"source={previous_source_code}->{source_code} "
            f"target={previous_target_code}->{target_code} "
            f"peer_source={previous_peer_source_code}->{peer_source_code} "
            f"peer_target={previous_peer_target_code}->{peer_target_code}"
        )
        self._log_detailed(
            f"[Dashboard] Language change detail: overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )

        # Check STT provider compatibility and show warning if needed
        stt_provider = settings.provider.stt.value
        warning = None
        if source_code != previous_source_code:
            warning = get_stt_compatibility_warning(source_code, stt_provider)
        if not warning and peer_source_code and peer_source_code != previous_peer_source_code:
            warning = get_stt_compatibility_warning(peer_source_code, stt_provider)
        if warning:
            snackbar = ft.SnackBar(
                ft.Text(t(warning.key, language=language_name(warning.language_code))),
                bgcolor=ft.Colors.ORANGE_700,
                duration=4000,
                behavior=ft.SnackBarBehavior.FLOATING,
                margin=ft.margin.only(bottom=90),
                padding=20,
            )
            self._mark_launch_high_priority_feedback_shown("stt_compatibility", snackbar)
            self.page.open(snackbar)

        # Coalesce rapid language switches (e.g. cycling favorites) into a single apply.
        # Each switch otherwise fires a full peer-pipeline rebuild, and a fast burst of
        # rebuilds can leave the peer STT re-initializing in a ~5s loop. We debounce so
        # only the final selection (after a short quiet period) actually applies.
        self._language_change_gen = getattr(self, "_language_change_gen", 0) + 1
        _params = dict(
            source_code=source_code,
            target_code=target_code,
            peer_source_code=peer_source_code,
            peer_target_code=peer_target_code,
            preset_index=preset_index,
            extra_target_codes=extra_target_codes,
            extra_peer_source_codes=extra_peer_source_codes,
        )

        async def _debounced_apply(_gen=self._language_change_gen, _p=_params):
            import asyncio
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                return
            if _gen != self._language_change_gen:
                return  # superseded by a newer switch

            async def _task():
                await self.controller.on_dashboard_language_change(**_p)

            self._queue_settings_mutation_task(_task)

        self.page.run_task(_debounced_apply)

    def _on_filter_peer_change(self, enabled: bool) -> None:
        async def _task():
            settings = self.controller.settings
            if settings is None:
                return
            import copy
            updated = copy.deepcopy(settings)
            updated.ui.filter_peer_by_target_languages = bool(enabled)
            await self.controller.apply_settings(updated)
        self._queue_settings_mutation_task(_task)

    def _stt_key_flags_from_settings(self, settings) -> dict:
        """Return {provider_value: True/False} indicating whether each STT provider has its API key set."""
        try:
            from puripuly_heart.config.settings import STTProviderName
            verified = getattr(settings, "api_key_verified", None)
            alibaba_ok = (
                bool(getattr(verified, "alibaba_beijing", False))
                or bool(getattr(verified, "alibaba_singapore", False))
            )
            return {
                STTProviderName.LOCAL_QWEN.value: True,  # no key needed
                STTProviderName.QWEN_ASR.value: alibaba_ok,
                STTProviderName.DEEPGRAM.value: bool(getattr(verified, "deepgram", False)),
                STTProviderName.SONIOX.value: bool(getattr(verified, "soniox", False)),
            }
        except Exception:
            return {}

    def _translator_key_flags_from_settings(self, settings) -> dict:
        """Return {model_value: True/False} indicating whether each translator model has its API key."""
        try:
            from puripuly_heart.config.settings import TranslationModel
            verified = getattr(settings, "api_key_verified", None)
            google_ok = bool(getattr(verified, "google", False))
            openrouter_ok = bool(getattr(verified, "openrouter", False))
            deepseek_ok = bool(getattr(verified, "deepseek", False))
            alibaba_ok = (
                bool(getattr(verified, "alibaba_beijing", False))
                or bool(getattr(verified, "alibaba_singapore", False))
            )
            deepl_ok = bool(getattr(verified, "deepl", False))
            return {
                TranslationModel.GEMMA4.value: True,              # managed (free)
                TranslationModel.DEEPSEEK_V4_FLASH.value: True,   # managed (free)
                TranslationModel.DEEPSEEK_V4_PRO.value: deepseek_ok,
                TranslationModel.GEMINI_3_FLASH.value: google_ok,
                TranslationModel.GEMINI_31_FLASH_LITE.value: google_ok,
                TranslationModel.QWEN_35_PLUS.value: openrouter_ok or alibaba_ok,
                TranslationModel.DEEPL.value: deepl_ok,
                TranslationModel.GOOGLE_TRANSLATE.value: True,    # free web
                TranslationModel.BING.value: True,                # free web
                TranslationModel.PAPAGO.value: True,              # free web
                TranslationModel.LOCAL_LLM.value: True,           # local, no key
            }
        except Exception:
            return {}

    def _on_settings_changed(self, settings) -> None:
        # For values that can be toggled from the dashboard (not just settings view),
        # merge the live controller state so a stale settings-view draft can't overwrite
        # a dashboard toggle the user just made.
        _live = getattr(self.controller, "settings", None)
        if settings is not None and _live is not None:
            try:
                if getattr(settings, "osc", None) and getattr(_live, "osc", None):
                    settings.osc.vrc_mic_intercept = _live.osc.vrc_mic_intercept
            except Exception:
                pass
            try:
                if getattr(settings, "ui", None) and getattr(_live, "ui", None):
                    settings.ui.chatbox_send_peer = _live.ui.chatbox_send_peer
            except Exception:
                pass

        # Sync transliteration display flags directly to dashboard
        dash = getattr(self, "view_dashboard", None)
        if dash is not None and settings is not None:
            _ui = getattr(settings, "ui", None)
            dash.show_pinyin = bool(getattr(_ui, "show_pinyin", False))
            dash.show_romaji = bool(getattr(_ui, "show_romaji", False))
            dash.send_pinyin = bool(getattr(_ui, "send_pinyin", False))
            dash.send_romaji = bool(getattr(_ui, "send_romaji", False))
            dash._show_pending_echo = bool(getattr(_ui, "show_pending_echo", True))
            new_chatbox_peer = bool(getattr(_ui, "chatbox_send_peer", False))
            if dash._chatbox_send_peer != new_chatbox_peer:
                dash._chatbox_send_peer = new_chatbox_peer
                try:
                    dash._refresh_chatbox_peer_btn()
                except Exception:
                    pass
            new_self_in_overlay = bool(getattr(_ui, "self_in_overlay", True))
            dash._self_in_overlay = new_self_in_overlay
            dash._typed_in_overlay = bool(getattr(_ui, "typed_in_overlay", True))
            # Initialize the overlay lock icon and VR/PC mode chip from settings at
            # startup so they reflect reality before the user first toggles overlay.
            try:
                _overlay = getattr(settings, "overlay", None)
                _desktop_flet = getattr(_overlay, "desktop_flet", None)
                set_locked = getattr(dash, "set_overlay_locked", None)
                if _desktop_flet is not None and callable(set_locked):
                    set_locked(bool(getattr(_desktop_flet, "locked", False)))
                if _overlay is not None:
                    dash._overlay_target_pref = (
                        "desktop"
                        if str(getattr(_overlay, "target", "steamvr")).lower() == "desktop"
                        else "steamvr"
                    )
                    dash._overlay_auto_switch = bool(getattr(_overlay, "auto_switch", True))
                    dash._overlay_single_turn = bool(getattr(_overlay, "single_turn_mode", True))
                    dash._overlay_show_original = effective_show_peer_original(settings)
                    dash._overlay_show_translation = bool(getattr(_overlay, "show_translation", True))
                    dash._overlay_show_romanization = bool(getattr(_overlay, "show_romanization", True))
            except Exception:
                pass
            try:
                self._sync_settings_overlay_runtime_state()
            except Exception:
                pass
            _audio = getattr(settings, "audio", None)
            _input_device = getattr(_audio, "input_device", "") or ""
            try:
                set_dev = getattr(dash, "set_stt_input_device", None)
                if callable(set_dev):
                    set_dev(_input_device)
            except Exception:
                pass
            _osc = getattr(settings, "osc", None)
            new_mute_sync = bool(getattr(_osc, "vrc_mic_intercept", False))
            if getattr(dash, "_vrc_mute_sync", None) != new_mute_sync:
                dash._vrc_mute_sync = new_mute_sync
                try:
                    dash._refresh_vrc_mute_sync_btn()
                except Exception:
                    pass
            self._sync_translator_label(settings)
            self._sync_stt_label(settings)
            try:
                set_flags = getattr(dash, "set_stt_key_flags", None)
                if callable(set_flags):
                    set_flags(self._stt_key_flags_from_settings(settings))
            except Exception:
                pass
            try:
                set_trans_flags = getattr(dash, "set_translator_key_flags", None)
                if callable(set_trans_flags):
                    set_trans_flags(self._translator_key_flags_from_settings(settings))
            except Exception:
                pass

        async def _task():
            await self.controller.apply_settings(settings)
            self._sync_microphone_test_dialog_if_inactive()

        self._queue_settings_mutation_task(_task)

    def _sync_dashboard_from_controller_settings(self) -> None:
        """Sync dashboard button states from the loaded controller settings on startup."""
        s = getattr(self.controller, "settings", None)
        if s is None:
            return
        dash = getattr(self, "view_dashboard", None)
        if dash is None:
            return
        try:
            new_mute_sync = bool(getattr(getattr(s, "osc", None), "vrc_mic_intercept", True))
            dash._vrc_mute_sync = new_mute_sync
            dash._refresh_vrc_mute_sync_btn()
        except Exception:
            pass
        try:
            new_peer = bool(getattr(getattr(s, "ui", None), "chatbox_send_peer", False))
            dash._chatbox_send_peer = new_peer
            dash._refresh_chatbox_peer_btn()
        except Exception:
            pass
        try:
            dash._loopback_selected_only = bool(
                getattr(getattr(s, "ui", None), "loopback_selected_languages_only", False)
            )
            dash._loopback_translation_only = bool(
                getattr(getattr(s, "ui", None), "chatbox_send_peer_translation_only", False)
            )
        except Exception:
            pass
        try:
            preset = getattr(getattr(getattr(s, "overlay", None), "desktop_flet", None), "size_preset", None)
            set_size = getattr(dash, "set_overlay_size_preset", None)
            if preset and callable(set_size):
                set_size(preset)
        except Exception:
            pass
        try:
            self._sync_stt_label(s)
        except Exception:
            pass
        try:
            set_stt_flags = getattr(dash, "set_stt_key_flags", None)
            if callable(set_stt_flags):
                set_stt_flags(self._stt_key_flags_from_settings(s))
        except Exception:
            pass
        try:
            set_trans_flags = getattr(dash, "set_translator_key_flags", None)
            if callable(set_trans_flags):
                set_trans_flags(self._translator_key_flags_from_settings(s))
        except Exception:
            pass

    def _sync_stt_label(self, settings) -> None:
        dash = getattr(self, "view_dashboard", None)
        if dash is None or settings is None:
            return
        try:
            from puripuly_heart.config.settings import STTProviderName
            from puripuly_heart.ui.i18n import provider_label
            provider = getattr(getattr(settings, "provider", None), "stt", None)
            if provider is None:
                return
            val = provider.value if hasattr(provider, "value") else str(provider)
            label = provider_label(val)
            set_fn = getattr(dash, "set_stt_provider_label", None)
            if callable(set_fn):
                set_fn(label, val)
            # Peer STT label (so the PEER tooltip shows its model like MIC does)
            peer_provider = getattr(getattr(settings, "provider", None), "peer_stt", None)
            if peer_provider is not None:
                pval = peer_provider.value if hasattr(peer_provider, "value") else str(peer_provider)
                set_peer_fn = getattr(dash, "set_peer_stt_provider_label", None)
                if callable(set_peer_fn):
                    set_peer_fn(provider_label(pval), pval)
        except Exception:
            pass

    def _sync_translator_label(self, settings) -> None:
        dash = getattr(self, "view_dashboard", None)
        if dash is None or settings is None:
            return
        try:
            from puripuly_heart.config.settings import TranslationModel
            from puripuly_heart.ui.views.settings import _TRANSLATION_MODEL_LABEL_KEYS
            from puripuly_heart.ui.i18n import t
            translation = getattr(settings, "translation", None)
            model_val = getattr(translation, "model", None)
            matched = None
            for m in TranslationModel:
                if m.value == model_val or m == model_val:
                    matched = m
                    break
            if matched is not None and matched in _TRANSLATION_MODEL_LABEL_KEYS:
                dash.set_translator_label(t(_TRANSLATION_MODEL_LABEL_KEYS[matched]), model_value=matched.value)
        except Exception:
            pass

    def _active_translator_is_deepl(self) -> bool:
        return self._current_translator_model_value() == "deepl"

    def _current_translator_model_value(self) -> str:
        """Live translation model value from settings (so the picker highlight is accurate)."""
        settings = getattr(self.controller, "settings", None)
        if settings is None:
            return ""
        model_val = getattr(getattr(settings, "translation", None), "model", None)
        if model_val is None:
            return ""
        return getattr(model_val, "value", None) or str(model_val)

    def _on_request_deepl_usage_refresh(self) -> None:
        try:
            self.page.run_task(self._refresh_deepl_usage_display)
        except Exception:
            logger.exception("Failed to refresh DeepL usage")

    async def _refresh_deepl_usage_display(self) -> None:
        dash = getattr(self, "view_dashboard", None)
        if dash is None:
            return
        set_usage = getattr(dash, "set_translator_usage", None)
        if not callable(set_usage):
            return
        # Only the DeepL translator exposes usage; clear it for anything else
        # (e.g. local Qwen STT on MIC/PEER has no API usage to show).
        if not self._active_translator_is_deepl():
            set_usage(None)
            return
        result = await self.controller.fetch_deepl_usage()
        from puripuly_heart.ui.i18n import t
        if result is None:
            set_usage(t("dashboard.deepl.usage.unavailable"))
            return
        used, limit = result
        if limit >= 1_000_000_000:
            # DeepL Pro / pay-as-you-go reports a 1,000,000,000 sentinel "no cap" limit.
            set_usage(t("dashboard.deepl.usage.nocap", used=f"{used:,}"))
        else:
            remaining = max(0, limit - used)
            set_usage(t(
                "dashboard.deepl.usage.capped",
                used=f"{used:,}", limit=f"{limit:,}", remaining=f"{remaining:,}",
            ))

    def _on_transliteration_change(
        self,
        show_pinyin: bool,
        send_pinyin: bool,
        show_romaji: bool,
        send_romaji: bool,
        show_latin: bool = False,
        send_latin: bool = False,
    ) -> None:
        import copy
        async def _task():
            settings = self.controller.settings
            if settings is None:
                return
            updated = copy.deepcopy(settings)
            updated.ui.show_pinyin = show_pinyin
            updated.ui.send_pinyin = send_pinyin
            updated.ui.show_romaji = show_romaji
            updated.ui.send_romaji = send_romaji
            updated.ui.show_latin = show_latin
            updated.ui.send_latin = send_latin
            await self.controller.apply_settings(updated)
        self._queue_settings_mutation_task(_task)

    def _on_pinyin_word_grouping_change(self, value: bool) -> None:
        import copy
        # Apply immediately so the very next transliteration uses the new mode.
        with contextlib.suppress(Exception):
            from puripuly_heart.core.transliteration import set_pinyin_word_grouping
            set_pinyin_word_grouping(bool(value))
        async def _task():
            settings = self.controller.settings
            if settings is None:
                return
            updated = copy.deepcopy(settings)
            updated.ui.pinyin_word_grouping = bool(value)
            await self.controller.apply_settings(updated)
        self._queue_settings_mutation_task(_task)

    # fmt id -> (include_source, send_reading, reading_only)
    _CHATBOX_FMT_FLAGS = {
        "orig_trans":      (True,  False, False),
        "orig_read_trans": (True,  True,  False),
        "read_trans":      (False, True,  False),
        "read_only":       (False, True,  True),
        "trans_only":      (False, False, False),
    }

    def _on_chatbox_format_change(self, fmt: str) -> None:
        import copy
        inc, read, ronly = self._CHATBOX_FMT_FLAGS.get(fmt, (True, False, False))
        # Apply to the live hub immediately so the next message uses the new format.
        with contextlib.suppress(Exception):
            hub = self.controller.hub
            hub.chatbox_include_source = inc
            hub.chatbox_reading_only = ronly
            hub.send_pinyin = hub.send_romaji = hub.send_latin = read
        async def _task():
            settings = self.controller.settings
            if settings is None:
                return
            updated = copy.deepcopy(settings)
            updated.osc.chatbox_include_source = inc
            updated.ui.send_pinyin = updated.ui.send_romaji = updated.ui.send_latin = read
            updated.ui.chatbox_reading_only = ronly
            await self.controller.apply_settings(updated)
        self._queue_settings_mutation_task(_task)

    def _on_translator_change(self, model_value: str) -> None:
        try:
            from puripuly_heart.config.settings import TranslationModel
            from puripuly_heart.ui.views.settings import _TRANSLATION_MODEL_LABEL_KEYS
            from puripuly_heart.ui.i18n import t
            import copy

            matched = None
            for m in TranslationModel:
                if m.value == model_value:
                    matched = m
                    break
            if matched is None:
                return

            current_settings = self.controller.settings
            updated = copy.deepcopy(current_settings)
            updated.translation.model = matched.value
            from puripuly_heart.config.settings import materialize_translation_settings
            materialize_translation_settings(updated)

            async def _task():
                await self.controller.apply_settings(updated)
                await self.controller.apply_providers(force_rebuild_llm=True)

            self._queue_settings_mutation_task(_task)

            # Sync label immediately
            dash = getattr(self, "view_dashboard", None)
            if dash is not None and matched in _TRANSLATION_MODEL_LABEL_KEYS:
                dash.set_translator_label(t(_TRANSLATION_MODEL_LABEL_KEYS[matched]), model_value=matched.value)
            # Refresh the DeepL usage strip (shows/hides depending on the new model)
            try:
                self.page.run_task(self._refresh_deepl_usage_display)
            except Exception:
                pass
        except Exception:
            pass

    def _on_request_stt_download(self) -> None:
        # NOTE: Flet dispatches on_click on a UI handler thread, not the asyncio
        # event-loop thread. _start_local_stt_download calls asyncio.create_task,
        # which requires a running loop in the current thread — so it must be run
        # via page.run_task (same mechanism every other dashboard action uses),
        # otherwise it raises "no running event loop" and the button does nothing.
        async def _task():
            start = getattr(self.controller, "_start_local_stt_download", None)
            if callable(start):
                start(origin="manual_notice_btn")

        try:
            self.page.run_task(_task)
        except Exception:
            logger.exception("Failed to start local STT model download")

    def _on_dashboard_stt_provider_change(self, provider_value: str) -> None:
        try:
            import copy
            from puripuly_heart.config.settings import STTProviderName
            from puripuly_heart.ui.i18n import provider_label
            current = self.controller.settings
            if current is None:
                return
            updated = copy.deepcopy(current)
            updated.provider.stt = STTProviderName(provider_value)
            # Update label immediately so UI reflects the change
            dash = getattr(self, "view_dashboard", None)
            if dash is not None:
                set_fn = getattr(dash, "set_stt_provider_label", None)
                if callable(set_fn):
                    set_fn(provider_label(provider_value), provider_value)
            try:
                sync_fn = getattr(self.view_settings, "sync_stt_provider_label", None)
                if callable(sync_fn):
                    sync_fn(provider_value)
            except Exception:
                pass

            async def _task():
                await self.controller.apply_settings(updated)
                await self.controller.apply_providers()

            self._queue_settings_mutation_task(_task)
        except Exception:
            pass

    def _on_dashboard_peer_stt_provider_change(self, provider_value: str) -> None:
        try:
            import copy
            from puripuly_heart.config.settings import STTProviderName
            current = self.controller.settings
            if current is None:
                return
            updated = copy.deepcopy(current)
            updated.provider.peer_stt = STTProviderName(provider_value)

            async def _task():
                await self.controller.apply_settings(updated)
                await self.controller.apply_providers()

            self._queue_settings_mutation_task(_task)
        except Exception:
            pass

    def _on_start_microphone_test(self) -> None:
        async def _task():
            dialog = self._get_microphone_test_dialog()
            dialog.reset()
            dialog.open()
            start_microphone_test = self.controller.start_microphone_test
            if _callable_accepts_keyword(start_microphone_test, "meter_callback"):
                start_result = start_microphone_test(meter_callback=dialog.set_level)
            else:
                start_result = start_microphone_test()
            started = await start_result if inspect.isawaitable(start_result) else start_result
            if not started:
                dialog.show_failure()
                return

        self._queue_settings_mutation_task(_task)

    def _on_stop_microphone_test(self) -> None:
        async def _task() -> None:
            stop_microphone_test = getattr(self.controller, "stop_microphone_test", None)
            if callable(stop_microphone_test):
                result = stop_microphone_test()
                if inspect.isawaitable(result):
                    await result
            self._close_microphone_test_dialog()

        self._queue_settings_mutation_task(_task)

    def _get_microphone_test_dialog(self) -> MicrophoneTestDialog:
        dialog = getattr(self, "_microphone_test_dialog", None)
        if dialog is None:
            dialog = MicrophoneTestDialog(
                self.page,
                on_close=self._on_microphone_test_dialog_dismiss,
            )
            self._microphone_test_dialog = dialog
        return dialog

    def _close_microphone_test_dialog(self) -> None:
        dialog = getattr(self, "_microphone_test_dialog", None)
        if dialog is None:
            return
        dialog.close(notify=False)
        dialog.reset()

    def _on_microphone_test_dialog_dismiss(self) -> None:
        self._on_stop_microphone_test()

    def _sync_microphone_test_dialog_if_inactive(self) -> None:
        controller = getattr(self, "controller", None)
        if bool(getattr(controller, "microphone_test_active", False)):
            return
        self._close_microphone_test_dialog()

    def _on_prompt_apply_settings(self, settings) -> None:
        async def _task():
            merged_settings = self.controller.merge_settings_tab_apply_with_current_languages(
                settings
            )
            await self.controller.apply_settings(merged_settings)

        self._queue_settings_mutation_task(_task)

    def _on_runtime_logging_mode_change(self, mode: str) -> None:
        self.controller.set_runtime_logging_mode(mode)
        self.view_logs.set_runtime_logging_mode(self.controller.runtime_logging_mode)

    def _on_providers_changed(self) -> None:
        pending_settings = None
        view_settings = getattr(self, "view_settings", None)
        consume_provider_apply_settings = getattr(
            view_settings,
            "consume_provider_apply_settings",
            None,
        )
        if callable(consume_provider_apply_settings) and getattr(
            view_settings,
            "has_provider_changes",
            False,
        ):
            pending_settings = consume_provider_apply_settings()
            view_settings.has_provider_changes = False

        async def _task():
            if pending_settings is None:
                await self.controller.apply_providers()
            else:
                await self.controller.apply_providers(pending_settings)

        self._queue_settings_mutation_task(_task)

    def _on_local_llm_secret_changed(self) -> None:
        async def _task():
            settings = getattr(self.controller, "settings", None)
            if settings is None or settings.provider.llm != LLMProviderName.LOCAL_LLM:
                return
            await self.controller.apply_providers(force_rebuild_llm=True)

        self._queue_settings_mutation_task(_task)

    def _on_request_openrouter_pkce(
        self,
        target_settings: AppSettings,
        *,
        launch_source: str = "settings",
    ) -> None:
        if getattr(self, "_openrouter_pkce_request_active", False):
            reopen_authorization_url = getattr(
                self.controller,
                "reopen_openrouter_pkce_authorization_url",
                None,
            )
            if callable(reopen_authorization_url):
                reopen_authorization_url()
            return
        self._openrouter_pkce_request_active = True

        async def _task() -> None:
            try:
                ok = await self.controller.connect_openrouter_via_pkce(
                    target_settings=target_settings,
                    launch_source=launch_source,
                )
                if ok:
                    refresh_after_openrouter_pkce_success = getattr(
                        self.view_settings,
                        "refresh_after_openrouter_pkce_success",
                        None,
                    )
                    if callable(refresh_after_openrouter_pkce_success):
                        refresh_after_openrouter_pkce_success(
                            self.controller.settings,
                            config_path=self.controller.config_path,
                        )
                    else:
                        self.view_settings.load_from_settings(
                            self.controller.settings,
                            config_path=self.controller.config_path,
                            preserve_custom_vocab_draft=True,
                        )
                    self._show_snackbar(t("openrouter.pkce.connected"), COLOR_SUCCESS)
            finally:
                self._openrouter_pkce_request_active = False

        self._queue_settings_mutation_task(_task)

    def _close_discord_managed_auth_dialog(self) -> None:
        dialog = getattr(self, "_discord_managed_auth_dialog", None)
        close = getattr(dialog, "close", None)
        if callable(close):
            close()

    def show_discord_managed_auth_dialog(self, preview: bool = False) -> None:
        if not preview:
            self._mark_launch_high_priority_feedback_shown("auth_required")
        if preview:
            on_continue = self._close_discord_managed_auth_dialog
            on_byok = self._close_discord_managed_auth_dialog
            on_close = self._close_discord_managed_auth_dialog
            on_reopen_browser = self._close_discord_managed_auth_dialog
            on_cancel = self._close_discord_managed_auth_dialog
        else:
            on_continue = self._start_discord_managed_auth
            on_byok = self._on_discord_managed_auth_byok
            on_close = self._close_discord_managed_auth_dialog
            on_reopen_browser = (
                self._reopen_discord_managed_auth_browser
                if self._supports_discord_managed_auth_reopen()
                else None
            )
            on_cancel = self._cancel_discord_managed_auth

        dialog = DiscordManagedAuthDialog(
            self.page,
            on_continue=on_continue,
            on_byok=on_byok,
            on_close=on_close,
            on_reopen_browser=on_reopen_browser,
            on_cancel=on_cancel,
        )
        self._discord_managed_auth_dialog = dialog
        dialog.open()

    def _run_optional_discord_auth_controller_hook(self, hook_name: str) -> None:
        controller = getattr(self, "controller", None)
        hook = getattr(controller, hook_name, None)
        if not callable(hook):
            return
        result = hook()
        if inspect.isawaitable(result):

            async def _task() -> None:
                await result

            self.page.run_task(_task)

    def _supports_discord_managed_auth_reopen(self) -> bool:
        controller = getattr(self, "controller", None)
        reopen = getattr(controller, "reopen_discord_managed_auth_browser", None)
        return callable(reopen)

    def _next_discord_managed_auth_generation(self) -> int:
        generation = int(getattr(self, "_discord_managed_auth_generation", 0)) + 1
        self._discord_managed_auth_generation = generation
        self._discord_managed_auth_cancelled = False
        return generation

    def _is_current_discord_managed_auth_generation(self, generation: int) -> bool:
        return bool(
            generation == getattr(self, "_discord_managed_auth_generation", None)
            and not getattr(self, "_discord_managed_auth_cancelled", False)
        )

    def _translation_enable_succeeded(self, controller: object, result: object) -> bool:
        if result is False:
            return False
        hub = getattr(controller, "hub", None)
        if hub is not None:
            return bool(
                getattr(hub, "llm", None) is not None and getattr(hub, "translation_enabled", False)
            )
        return result is True

    def _start_discord_managed_auth(self) -> None:
        dialog = getattr(self, "_discord_managed_auth_dialog", None)
        raw_referral_id = getattr(dialog, "referral_id", "")
        referral_id = (
            raw_referral_id if isinstance(raw_referral_id, str) and raw_referral_id else None
        )
        set_waiting = getattr(dialog, "set_waiting", None)
        if callable(set_waiting):
            set_waiting()
        generation = self._next_discord_managed_auth_generation()

        async def _task() -> None:
            controller = getattr(self, "controller", None)
            start_auth = getattr(controller, "start_discord_managed_auth_from_dialog", None)
            if not callable(start_auth):
                return

            def _mark_callback_received() -> None:
                self.mark_discord_managed_auth_callback_received(generation)

            try:
                ok = await start_auth(
                    on_callback_received=_mark_callback_received,
                    referral_id=referral_id,
                )
                if not ok or not self._is_current_discord_managed_auth_generation(generation):
                    return
                enable_translation = getattr(controller, "set_translation_enabled", None)
                if not callable(enable_translation):
                    return
                enable_result = await enable_translation(True)
                if not self._is_current_discord_managed_auth_generation(generation):
                    return
                if not self._translation_enable_succeeded(controller, enable_result):
                    return
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Discord managed auth task failed")
                return
            self._close_discord_managed_auth_dialog()
            self._show_snackbar(t("discord_auth.success"), COLOR_SUCCESS)
            if (
                getattr(controller, "last_discord_managed_auth_referral_bonus_applied", False)
                is True
            ):
                self._show_snackbar(t("discord_auth.referral_reward_applied"), COLOR_SUCCESS)
            self._set_dashboard_translation_visual_state(True)
            if self._is_current_discord_managed_auth_generation(generation):
                self._discord_managed_auth_task_handle = None

        self._discord_managed_auth_task_handle = self.page.run_task(_task)

    def mark_discord_managed_auth_callback_received(self, generation: int | None = None) -> None:
        if generation is not None and not self._is_current_discord_managed_auth_generation(
            generation
        ):
            return
        dialog = getattr(self, "_discord_managed_auth_dialog", None)
        if getattr(dialog, "is_open", True) is False:
            return
        if getattr(dialog, "is_waiting", True) is False:
            return
        set_callback_received = getattr(dialog, "set_callback_received", None)
        if callable(set_callback_received):
            set_callback_received()

    def _reopen_discord_managed_auth_browser(self) -> None:
        self._run_optional_discord_auth_controller_hook("reopen_discord_managed_auth_browser")

    def _cancel_discord_managed_auth(self) -> None:
        self._discord_managed_auth_cancelled = True
        task_handle = getattr(self, "_discord_managed_auth_task_handle", None)
        cancel = getattr(task_handle, "cancel", None)
        if callable(cancel):
            with contextlib.suppress(Exception):
                cancel()
        self._discord_managed_auth_task_handle = None
        self._close_discord_managed_auth_dialog()

    def _build_managed_openrouter_byok_target_settings(self) -> AppSettings | None:
        current_settings = getattr(getattr(self, "controller", None), "settings", None)
        if current_settings is None:
            return None
        if current_settings.provider.llm != LLMProviderName.OPENROUTER:
            return None
        if current_settings.openrouter.selected_source != OpenRouterCredentialSource.MANAGED:
            return None

        openrouter_model = None
        selection_alias = current_settings.openrouter.selection_alias
        if selection_alias is not None:
            try:
                profile = profile_for_alias(selection_alias.value)
            except KeyError:
                profile = None
            if profile is not None:
                openrouter_model = profile.openrouter_model
        if openrouter_model is None:
            openrouter_model = current_settings.openrouter.llm_model.value

        alias_value = get_openrouter_selection_alias_for_model_and_source(
            openrouter_model,
            OpenRouterCredentialSource.BYOK.value,
        )
        if alias_value is None:
            return None

        target_settings = copy.deepcopy(current_settings)
        target_settings.provider.llm = LLMProviderName.OPENROUTER
        target_settings.openrouter.selection_alias = OpenRouterSelectionAlias(alias_value)
        target_settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
        target_settings.openrouter.llm_model = OpenRouterLLMModel(openrouter_model)
        target_settings.openrouter.provider_routing = OpenRouterProviderRouting.DEFAULT
        target_settings.translation.connection = TranslationConnection.OPENROUTER
        target_settings.translation.connection_history[target_settings.translation.model.value] = (
            TranslationConnection.OPENROUTER
        )
        return target_settings

    def _build_founder_letter_target_settings(self) -> AppSettings | None:
        return self._build_managed_openrouter_byok_target_settings()

    def _on_discord_managed_auth_byok(self) -> None:
        target_settings = self._build_managed_openrouter_byok_target_settings()
        if target_settings is None:
            self._show_snackbar(t("openrouter.pkce.failed"), ft.Colors.ORANGE_700)
            return
        self._on_request_openrouter_pkce(target_settings, launch_source="discord_auth")

    def _on_founder_letter_connect(self) -> None:
        target_settings = self._build_founder_letter_target_settings()
        if target_settings is None:
            self._show_snackbar(t("openrouter.pkce.failed"), ft.Colors.ORANGE_700)
            return
        self._on_request_openrouter_pkce(target_settings, launch_source="letter")

    def _on_founder_letter_contact(self) -> None:
        webbrowser.open(FOUNDER_CONTACT_URL)

    def _on_founder_letter_readme(self) -> None:
        webbrowser.open(founder_readme_url_for_locale(get_locale()))

    def show_founder_letter_dialog(self) -> None:
        self._mark_launch_high_priority_feedback_shown("usage_exhaustion")
        dialog = FounderLetterDialog(self.page, on_readme=self._on_founder_letter_readme)
        self._founder_letter_dialog = dialog
        dialog.open()

    def _api_key_verification_matches_current_field(self, provider: str, key: str) -> bool:
        field_by_provider = {
            "deepgram": "_deepgram_key",
            "soniox": "_soniox_key",
            "google": "_google_key",
            "openrouter": "_openrouter_key",
            "deepseek": "_deepseek_key",
            "alibaba_beijing": "_alibaba_key_beijing",
            "alibaba_singapore": "_alibaba_key_singapore",
        }
        field_name = field_by_provider.get(provider)
        if field_name is None:
            return True

        field = getattr(getattr(self, "view_settings", None), field_name, None)
        if field is None:
            return True

        current_key = getattr(field, "value", None)
        if current_key is None:
            return True

        return current_key == key

    async def _on_verify_api_key(self, provider: str, key: str) -> tuple[bool, str]:
        success, msg = await self.controller.verify_api_key(provider, key)

        if not self._api_key_verification_matches_current_field(provider, key):
            return success, msg

        # Save verification result to settings
        setattr(self.controller.settings.api_key_verified, provider, success)
        save_settings(self.controller.config_path, self.controller.settings)

        # Sync verification result with dashboard needs_key flags (UI update on user click)
        if provider in ("deepgram", "soniox", "qwen_asr"):
            self.view_dashboard.set_stt_needs_key(not success, update_ui=False)
            try:
                set_flags = getattr(self.view_dashboard, "set_stt_key_flags", None)
                if callable(set_flags):
                    set_flags(self._stt_key_flags_from_settings(self.controller.settings))
            except Exception:
                pass
        elif provider in (
            "google",
            "openrouter",
            "deepseek",
            "alibaba_beijing",
            "alibaba_singapore",
            "deepl",
        ):
            self.view_dashboard.set_translation_needs_key(not success, update_ui=False)
            try:
                set_trans_flags = getattr(self.view_dashboard, "set_translator_key_flags", None)
                if callable(set_trans_flags):
                    set_trans_flags(self._translator_key_flags_from_settings(self.controller.settings))
            except Exception:
                pass

        return success, msg

    def _on_secret_cleared(self, key: str) -> None:
        """Reset verification status when API key is cleared."""
        # Map secret key name to provider name
        key_to_provider = {
            "deepgram_api_key": "deepgram",
            "soniox_api_key": "soniox",
            "google_api_key": "google",
            "openrouter_api_key": "openrouter",
            "deepseek_api_key": "deepseek",
            "alibaba_api_key": "alibaba_beijing",  # Use beijing as default
            "alibaba_api_key_beijing": "alibaba_beijing",
            "alibaba_api_key_singapore": "alibaba_singapore",
            "deepl_api_key": "deepl",
        }
        provider = key_to_provider.get(key)
        if provider:
            setattr(self.controller.settings.api_key_verified, provider, False)
            save_settings(self.controller.config_path, self.controller.settings)

            # Update dashboard needs_key flag
            if provider in ("deepgram", "soniox"):
                self.view_dashboard.set_stt_needs_key(True, update_ui=False)
                try:
                    set_flags = getattr(self.view_dashboard, "set_stt_key_flags", None)
                    if callable(set_flags):
                        set_flags(self._stt_key_flags_from_settings(self.controller.settings))
                except Exception:
                    pass
            elif provider in (
                "google",
                "openrouter",
                "deepseek",
                "alibaba_beijing",
                "alibaba_singapore",
                "deepl",
            ):
                self.view_dashboard.set_translation_needs_key(True, update_ui=False)
                try:
                    set_trans_flags = getattr(self.view_dashboard, "set_translator_key_flags", None)
                    if callable(set_trans_flags):
                        set_trans_flags(self._translator_key_flags_from_settings(self.controller.settings))
                except Exception:
                    pass

    def _show_snackbar(self, message: str, bgcolor, duration: int = 4000) -> None:
        """Show a snackbar above the bottom nav."""
        snackbar = ft.SnackBar(
            ft.Text(message, size=18, color=ft.Colors.WHITE),
            bgcolor=bgcolor,
            duration=duration,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.margin.only(bottom=90),
            padding=20,
        )
        self._mark_launch_high_priority_feedback_shown("snackbar", snackbar)
        self.page.open(snackbar)

    def on_overlay_state_changed(
        self,
        *,
        state: str,
        failure_reason: str | None = None,
    ) -> None:
        previous_state = getattr(self, "overlay_state", "unknown")
        self._log_basic(f"[Overlay] State changed: {previous_state} -> {state}")
        self.overlay_state = state
        self.overlay_failure_reason = failure_reason
        self._log_detailed(
            f"[Overlay] State detail: overlay_state={state} failure_reason={failure_reason}"
        )
        self._sync_settings_overlay_runtime_state()
        self.refresh_overlay_peer_contract()


async def main_gui(page: ft.Page, *, config_path, debug_ui_preview: bool = False):
    # Apply the saved UI locale BEFORE building the views, so every t() call at
    # construction renders in the right language. The controller only set the locale
    # later (during start()), which left many construction-time labels, tooltips, and
    # buttons in English because apply_locale() only re-translates some of them.
    with contextlib.suppress(Exception):
        from puripuly_heart.config.settings import load_settings
        from puripuly_heart.ui.i18n import set_locale as _set_locale_early

        _early_locale = load_settings(config_path).ui.locale
        if _early_locale:
            _set_locale_early(_early_locale)

    app = TranslatorApp(
        page,
        config_path=config_path,
        debug_ui_preview=debug_ui_preview,
    )
    await app.controller.start()

    # Sync dashboard button states from loaded settings (mute sync, loopback, STT label)
    try:
        app._sync_dashboard_from_controller_settings()
    except Exception:
        pass

    # Populate the DeepL usage strip if DeepL is the active translator on startup
    try:
        app.page.run_task(app._refresh_deepl_usage_display)
    except Exception:
        pass

    # Wire VRChat OSC mute state callback → dashboard orange "syncing" indicator
    try:
        _vms = getattr(app.controller, "vrc_mic_state", None)
        if _vms is not None:
            _vms.on_state_changed = app._on_vrc_mute_osc_state_changed
    except Exception:
        pass

    # Restore saved window size (settings loaded by controller.start)
    try:
        _ui = getattr(getattr(app.controller, "settings", None), "ui", None)
        if _ui is not None:
            _w = getattr(_ui, "window_width", 0) or 0
            _h = getattr(_ui, "window_height", 0) or 0
            if _w >= MIN_WINDOW_WIDTH:
                app.page.window.width = _w
            if _h >= MIN_WINDOW_HEIGHT:
                app.page.window.height = _h
            if _w >= MIN_WINDOW_WIDTH or _h >= MIN_WINDOW_HEIGHT:
                app.page.update()
    except Exception:
        pass

    # Check for updates in background
    update_kwargs = {"log_detailed": app._log_detailed}
    try:
        update_parameters = inspect.signature(_check_and_notify_update).parameters
    except (TypeError, ValueError):
        update_parameters = {}
    if "on_launch_snackbar_shown" in update_parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in update_parameters.values()
    ):
        update_kwargs["on_launch_snackbar_shown"] = (
            lambda snackbar: app._mark_launch_high_priority_feedback_shown("update", snackbar)
        )
    await _check_and_notify_update(page, **update_kwargs)

    # GitHub star prompt disabled


async def _check_and_notify_update(
    page: ft.Page,
    log_detailed=None,
    on_launch_snackbar_shown=None,
) -> None:
    """Check for updates and show notification as a toast."""
    try:
        update_info = await check_for_update()
        if update_info is None:
            return

        def _open_download(_e):
            webbrowser.open(update_info.download_url)
            snackbar.open = False
            page.update()

        snackbar = ft.SnackBar(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        name=ft.Icons.SYSTEM_UPDATE,
                        color=ft.Colors.WHITE,
                        size=28,
                    ),
                    ft.Text(
                        t("update.available", version=update_info.version),
                        color=ft.Colors.WHITE,
                        size=18,
                        font_family=font_for_language(get_locale()),
                        expand=True,
                    ),
                    ft.TextButton(
                        text=t("update.download"),
                        on_click=_open_download,
                        style=ft.ButtonStyle(
                            color=ft.Colors.WHITE,
                            text_style=ft.TextStyle(
                                size=18,
                                font_family=font_for_language(get_locale()),
                            ),
                            overlay_color=COLOR_PRIMARY,
                        ),
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            bgcolor=COLOR_SUCCESS,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.margin.only(bottom=90),
            padding=20,
            duration=30000,  # 30초
            show_close_icon=True,
            close_icon_color=ft.Colors.WHITE,
        )
        page.open(snackbar)
        if callable(on_launch_snackbar_shown):
            on_launch_snackbar_shown(snackbar)

    except Exception as exc:
        message = f"[Update] Check notification failed: {exc}"
        if callable(log_detailed):
            log_detailed(message)
            return
        logger.debug(message)
