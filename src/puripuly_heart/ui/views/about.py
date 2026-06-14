"""About page view with version, credits, acknowledgments, and license info."""

import webbrowser
from importlib import resources

import flet as ft

from puripuly_heart import GITHUB_REPO, __version__
from puripuly_heart.ui.components.shared_card_wrapper import SharedCardWrapper
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
)

_CENTER_ALIGNMENT = ft.alignment.Alignment(0, 0)


def _load_third_party_notices() -> str:
    """Load THIRD_PARTY_NOTICES.txt from package data."""
    try:
        return (
            resources.files("puripuly_heart.data")
            .joinpath("THIRD_PARTY_NOTICES.txt")
            .read_text(encoding="utf-8")
        )
    except Exception:
        return "Could not load license information."


def _get_profile_image_path() -> str:
    """Get the profile image path from package data."""
    try:
        return str(resources.files("puripuly_heart.data.pictures").joinpath("salee_pic.png"))
    except Exception:
        return ""


class AboutView(ft.Column):
    """About page with version, credits, inspired by, special thanks, and licenses."""

    def __init__(self):
        super().__init__(expand=True, scroll=ft.ScrollMode.AUTO, spacing=16)

        self._build_ui()

    def _build_ui(self):
        self.controls = [
            self._build_header(),
            self._build_licenses_card(),
        ]

    def _build_header(self) -> ft.Control:
        profile_path = _get_profile_image_path()
        profile_image = ft.Container(
            content=(
                ft.Image(src=profile_path, width=40, height=40, fit=ft.ImageFit.COVER, border_radius=20)
                if profile_path
                else ft.Icon(ft.Icons.PERSON, size=28, color=COLOR_ON_BACKGROUND)
            ),
            width=40, height=40, border_radius=20, bgcolor=COLOR_DIVIDER,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

        inspiration_projects = [
            ("VRCT", "https://github.com/misyaguziya/VRCT"),
            ("mimiuchi", "https://github.com/naeruru/mimiuchi"),
            ("Yakutan", "https://github.com/febilly/Yakutan"),
        ]
        inspiration_chips = [
            ft.Container(
                content=ft.Text(name, size=12, color=COLOR_ON_BACKGROUND),
                on_click=lambda _, u=url: webbrowser.open(u),
                on_hover=self._on_link_hover,
                border=ft.border.all(1, COLOR_DIVIDER),
                border_radius=6,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
            )
            for name, url in inspiration_projects
        ]

        thanks_names = [t(k) for k in self._SPECIAL_THANKS_NAME_KEYS] + ["and you!"]
        thanks_text = "  ·  ".join(thanks_names)

        def _link_chip(label: str, url: str) -> ft.Container:
            txt = ft.Text(label, size=12, color=COLOR_PRIMARY)
            return ft.Container(
                content=txt,
                on_click=lambda _, u=url: webbrowser.open(u),
                on_hover=lambda e, t=txt: (
                    setattr(t, "color", COLOR_PRIMARY if e.data == "true" else COLOR_NEUTRAL)
                    or (t.update() if t.page else None)
                ),
            )

        return ft.Container(
            content=ft.Column(
                [
                    # ── App name + version ────────────────────────────────────
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(
                                        "PuriPulyHeart+",
                                        size=22, weight=ft.FontWeight.BOLD, color=COLOR_PRIMARY,
                                    ),
                                    ft.Text(
                                        "A fork of PuriPuly Heart with additional features",
                                        size=11, color=COLOR_NEUTRAL,
                                    ),
                                ],
                                spacing=1, tight=True,
                            ),
                            ft.Container(expand=True),
                            ft.Container(
                                content=ft.Text(f"v{__version__}", size=13, color=COLOR_NEUTRAL),
                                on_click=lambda _: webbrowser.open(f"https://github.com/{GITHUB_REPO}"),
                                on_hover=self._on_version_hover,
                                tooltip="View releases on GitHub",
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, color=COLOR_DIVIDER, thickness=1),

                    # ── Original author ───────────────────────────────────────
                    ft.Row(
                        [
                            profile_image,
                            ft.Container(width=10),
                            ft.Column(
                                [
                                    ft.Text("Original project by", size=11, color=COLOR_NEUTRAL),
                                    ft.Container(
                                        content=ft.Text(
                                            "salee (kapitalismho)",
                                            size=13, weight=ft.FontWeight.W_600, color=COLOR_ON_BACKGROUND,
                                        ),
                                        on_click=lambda _: webbrowser.open("https://x.com/kapitalismho"),
                                        on_hover=self._on_name_hover,
                                        tooltip="Open salee's Twitter/X",
                                    ),
                                    _link_chip(
                                        "PuriPuly Heart — original repository",
                                        "https://github.com/kapitalismho/PuriPuly-heart",
                                    ),
                                ],
                                spacing=3,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        spacing=0,
                    ),
                    ft.Divider(height=1, color=COLOR_DIVIDER, thickness=1),

                    # ── About this fork ───────────────────────────────────────
                    ft.Column(
                        [
                            ft.Text("About this fork", size=11, color=COLOR_NEUTRAL),
                            ft.Text(
                                "PuriPulyHeart+ is a fork of PuriPuly Heart built with gratitude to the "
                                "original project. It adds optional features like transcription logging and "
                                "expanded language options for users who want them. The original is wonderful "
                                "and none of this would exist without it. Released under AGPL-3.0.",
                                size=12, color=COLOR_ON_BACKGROUND, no_wrap=False,
                            ),
                            ft.Row(
                                [
                                    _link_chip("Fork source", f"https://github.com/{GITHUB_REPO}"),
                                    _link_chip("Original project", "https://github.com/kapitalismho/PuriPuly-heart"),
                                ],
                                spacing=12,
                            ),
                        ],
                        spacing=6,
                    ),
                    ft.Divider(height=1, color=COLOR_DIVIDER, thickness=1),

                    # ── UI design credit (VRCT) ───────────────────────────────
                    ft.Column(
                        [
                            ft.Text("UI design", size=11, color=COLOR_NEUTRAL),
                            ft.Text(
                                "Visual design (dark palette, teal accent, sidebar layout) is heavily inspired "
                                "by VRCT by misyaguziya. No VRCT source code was used.",
                                size=12, color=COLOR_ON_BACKGROUND, no_wrap=False,
                            ),
                            _link_chip("VRCT by misyaguziya", "https://github.com/misyaguziya/VRCT"),
                        ],
                        spacing=6,
                    ),
                    ft.Divider(height=1, color=COLOR_DIVIDER, thickness=1),

                    # ── Special thanks ────────────────────────────────────────
                    ft.Column(
                        [
                            ft.Text(t("about.special_thanks"), size=11, color=COLOR_NEUTRAL),
                            ft.Text(thanks_text, size=12, color=COLOR_ON_BACKGROUND, no_wrap=False),
                        ],
                        spacing=4,
                    ),
                ],
                spacing=10,
            ),
            bgcolor=COLOR_SURFACE,
            border_radius=12,
            padding=ft.padding.all(16),
        )

    def _build_credits_card(self) -> ft.Control:
        return ft.Container()

    def _build_inspired_by_card(self) -> ft.Control:
        return ft.Container()

    # Special thanks name keys - add new names here and update locale bundles
    _SPECIAL_THANKS_NAME_KEYS = [
        "about.special_thanks.name.sui_32c",
        "about.special_thanks.name.nagikokoro",
        "about.special_thanks.name.motoka96",
        "about.special_thanks.name.ykol",
        "about.special_thanks.name.kascr",
        "about.special_thanks.name.just_monika_v",
        "about.special_thanks.name.fluvia",
        "about.special_thanks.name.han_chole",
        "about.special_thanks.name.ea_pe",
        "about.special_thanks.name.ephedrine",
    ]

    def _build_special_thanks_card(self) -> ft.Control:
        return ft.Container()

    def _build_licenses_card(self) -> ft.Control:
        """Build Open Source Licenses section."""
        licenses_text = _load_third_party_notices()

        card_content = ft.Column(
            controls=[
                ft.Text(
                    t("about.licenses"),
                    size=24,
                    weight=ft.FontWeight.BOLD,
                    color=COLOR_NEUTRAL,
                ),
                ft.Container(height=16),
                ft.Container(
                    content=ft.Text(
                        licenses_text,
                        size=16,
                        color=COLOR_ON_BACKGROUND,
                        selectable=True,
                    ),
                    width=float("inf"),
                    border=ft.border.all(1, COLOR_DIVIDER),
                    border_radius=12,
                    padding=16,
                    bgcolor=COLOR_SURFACE,
                ),
            ],
        )

        return self._wrap_card(card_content, height=None)

    def _wrap_card(
        self,
        content: ft.Control,
        *,
        height: float | int | None = SharedCardWrapper.DEFAULT_HEIGHT,
    ) -> SharedCardWrapper:
        return SharedCardWrapper(
            content,
            height=height,
        )

    def _on_name_hover(self, e):
        """Handle hover on name link."""
        text = e.control.content
        text.color = COLOR_PRIMARY if e.data == "true" else COLOR_ON_BACKGROUND
        text.update()

    def _on_link_hover(self, e):
        """Handle hover on project links."""
        text = e.control.content
        text.color = COLOR_PRIMARY if e.data == "true" else COLOR_ON_BACKGROUND
        text.update()

    def _on_version_hover(self, e):
        """Handle hover on version link."""
        text = e.control.content
        text.color = COLOR_PRIMARY if e.data == "true" else COLOR_ON_BACKGROUND
        text.update()

    def _on_thanks_hover(self, e):
        """Handle hover on thanks text."""
        text = e.control.content
        text.color = COLOR_PRIMARY if e.data == "true" else COLOR_ON_BACKGROUND
        text.update()

    def apply_locale(self) -> None:
        """Refresh UI text when locale changes."""
        self._build_ui()
        self.update()
