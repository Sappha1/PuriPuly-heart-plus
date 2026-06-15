"""API key input field with auto-verification on blur."""

from __future__ import annotations

import hashlib
from typing import Callable

import flet as ft
from flet import Colors as colors
from flet import Icons as icons

from puripuly_heart.ui.i18n import provider_label, t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_NEUTRAL_DARK,
    COLOR_PRIMARY,
    COLOR_WARNING,
)


class ApiKeyField(ft.Row):
    """API key input field with auto-verification on blur and status indicator."""

    def __init__(
        self,
        label_key: str,
        secret_key: str,
        provider: str,
        on_verify: Callable[[str, str], object] | None = None,
        on_save: Callable[[str, str], None] | None = None,
        show_snackbar: Callable[[str, str], None] | None = None,
        show_status: bool = True,
    ):
        self._label_key = label_key
        self._secret_key = secret_key
        self._provider = provider
        self._on_verify = on_verify
        self._on_save = on_save
        self._show_snackbar_cb = show_snackbar
        self._show_status = show_status
        self._dirty = False
        self._last_verified_hash = ""
        self._is_verifying = False

        # Custom reveal password toggle button
        self._reveal_button = ft.IconButton(
            icon=icons.VISIBILITY_OFF_ROUNDED,
            icon_color=COLOR_DIVIDER,
            icon_size=16,
            on_click=self._toggle_password_visibility,
        )

        self._text_field = ft.TextField(
            label=t(label_key),
            password=True,
            can_reveal_password=False,
            on_blur=self._handle_blur,
            on_change=self._handle_change,
            border_radius=8,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=14,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=13, weight=ft.FontWeight.W_500, color=COLOR_NEUTRAL_DARK),
            suffix=self._reveal_button,
            dense=True,
            content_padding=ft.padding.symmetric(horizontal=12, vertical=10),
        )

        self._current_status = "idle"
        self._status_icon = ft.Icon(
            name=icons.HELP_OUTLINE_ROUNDED,
            color=COLOR_NEUTRAL,
            size=20,
            tooltip=t("api_key.status.idle"),
        )

        controls: list[ft.Control] = [self._text_field]
        if self._show_status:
            controls.append(self._status_icon)

        super().__init__(
            controls=controls,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    @property
    def value(self) -> str:
        """Get current field value."""
        return self._text_field.value or ""

    @value.setter
    def value(self, val: str) -> None:
        """Set field value."""
        self._text_field.value = val
        self._dirty = False
        if self._text_field.page:
            self._text_field.update()

    def _get_key_hash(self, key: str) -> str:
        """Get SHA-256 hash of the key."""
        if not key:
            return ""
        return hashlib.sha256(key.encode()).hexdigest()

    def _toggle_password_visibility(self, e) -> None:
        """Toggle password visibility and update eye icon."""
        self._text_field.password = not self._text_field.password
        self._reveal_button.icon = (
            icons.VISIBILITY_OFF_ROUNDED if self._text_field.password else icons.VISIBILITY_ROUNDED
        )
        if self._text_field.page:
            self._text_field.update()
            self._reveal_button.update()

    def _handle_change(self, e) -> None:
        """Mark the field dirty after user edits."""
        _ = e
        self._dirty = True

    def _set_status(self, status: str) -> None:
        """Update status icon based on verification state."""
        if not self._show_status:
            return

        icon_map = {
            "idle": (icons.HELP_OUTLINE_ROUNDED, COLOR_NEUTRAL, "api_key.status.idle"),
            "verifying": (icons.HOURGLASS_TOP_ROUNDED, COLOR_NEUTRAL, "api_key.status.verifying"),
            "success": (icons.CHECK_CIRCLE_ROUNDED, COLOR_PRIMARY, "api_key.status.success"),
            "error": (icons.WARNING_ROUNDED, COLOR_WARNING, "api_key.status.error"),
        }
        icon, color, tooltip_key = icon_map.get(status, icon_map["idle"])
        self._current_status = status
        self._status_icon.name = icon
        self._status_icon.color = color
        self._status_icon.tooltip = t(tooltip_key)
        if self._status_icon.page:
            self._status_icon.update()

    def _handle_blur(self, e) -> None:
        """Handle blur event - save and verify."""
        key = self.value

        if self._dirty:
            self._dirty = False
            # Save user-edited values on blur.
            if self._on_save:
                self._on_save(self._secret_key, key)
        elif not self._show_status:
            return

        # Skip verification if this field hides status or has no callback.
        if not self._show_status or not self._on_verify:
            return

        if not key:
            self._set_status("idle")
            self._last_verified_hash = ""
            return

        key_hash = self._get_key_hash(key)
        if key_hash == self._last_verified_hash:
            return  # Skip if same key

        self._pending_key = key
        self._pending_hash = key_hash
        if self._is_verifying:
            return

        if self.page:
            self.page.run_task(self._run_verification)

    async def _run_verification(self) -> None:
        """Wrapper for run_task compatibility."""
        while True:
            key = getattr(self, "_pending_key", "")
            key_hash = getattr(self, "_pending_hash", "")
            if not key_hash:
                return

            self._pending_key = ""
            self._pending_hash = ""
            await self._verify_async(key, key_hash)

    async def _verify_async(self, key: str, key_hash: str) -> None:
        """Run verification asynchronously."""
        self._is_verifying = True
        self._set_status("verifying")

        try:
            success, msg = await self._on_verify(self._provider, key)
            if self._get_key_hash(self.value) != key_hash:
                return

            if success:
                self._set_status("success")
                self._last_verified_hash = key_hash
                self._show_snackbar(
                    t("snackbar.verification_ok", provider=provider_label(self._provider)),
                    colors.GREEN_400,
                )
            else:
                self._set_status("error")
                self._last_verified_hash = ""
                self._show_snackbar(
                    t("snackbar.verification_failed", message=self._translate_error(msg)),
                    colors.RED_400,
                )
        except Exception as exc:
            if self._get_key_hash(self.value) != key_hash:
                return

            self._set_status("error")
            self._last_verified_hash = ""
            self._show_snackbar(
                t("snackbar.verification_error", message=self._translate_error(str(exc))),
                colors.RED_400,
            )
        finally:
            self._is_verifying = False

    def _show_snackbar(self, message: str, bgcolor) -> None:
        """Show a toast via App-level callback or fallback to page."""
        if self._show_snackbar_cb:
            self._show_snackbar_cb(message, bgcolor)
        elif self.page:
            self.page.open(
                ft.SnackBar(
                    ft.Text(message, size=18, color=ft.Colors.WHITE),
                    bgcolor=bgcolor,
                    duration=4000,
                    behavior=ft.SnackBarBehavior.FLOATING,
                    margin=ft.margin.only(bottom=90),
                    padding=20,
                )
            )

    def _translate_error(self, msg: str) -> str:
        """Translate common error messages to user-friendly text."""
        msg_lower = msg.lower()
        if msg_lower.startswith("qwen_model_unavailable:"):
            model = msg.split(":", 1)[1].strip() if ":" in msg else ""
            return t("error.qwen_model_unavailable", model=model or "unknown")
        if "401" in msg or "unauthorized" in msg_lower:
            return t("error.api_key_invalid")
        if "403" in msg or "forbidden" in msg_lower:
            return t("error.api_key_invalid")
        if "timeout" in msg_lower or "timed out" in msg_lower:
            return t("error.network_timeout")
        if "connection" in msg_lower or "network" in msg_lower:
            return t("error.network_error")
        return msg

    def apply_locale(self) -> None:
        """Update labels and tooltips when locale changes."""
        self._text_field.label = t(self._label_key)
        if self._show_status:
            # Update tooltip based on current status
            tooltip_keys = {
                "idle": "api_key.status.idle",
                "verifying": "api_key.status.verifying",
                "success": "api_key.status.success",
                "error": "api_key.status.error",
            }
            tooltip_key = tooltip_keys.get(self._current_status, "api_key.status.idle")
            self._status_icon.tooltip = t(tooltip_key)
        if self.page:
            self._text_field.update()
            if self._show_status:
                self._status_icon.update()
