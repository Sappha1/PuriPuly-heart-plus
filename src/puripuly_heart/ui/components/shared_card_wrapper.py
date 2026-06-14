from __future__ import annotations

import flet as ft

from puripuly_heart.ui.components.glow import GLOW_CARD, create_glow_stack
from puripuly_heart.ui.theme import COLOR_SURFACE, get_card_shadow


class SharedCardWrapper(ft.Container):
    DEFAULT_HEIGHT = 180

    def __init__(
        self,
        content: ft.Control,
        *,
        expand: bool | None = None,
        height: float | int | None = DEFAULT_HEIGHT,
        padding: float | int = 24,
    ) -> None:
        resolved_expand = height is not None if expand is None else expand
        resolved_height = self.DEFAULT_HEIGHT if resolved_expand and height is None else height
        content_with_glow = create_glow_stack(
            ft.Container(content=content, expand=resolved_expand, padding=padding),
            config=GLOW_CARD,
        )
        content_with_glow.expand = resolved_expand
        content_with_glow.controls[1].expand = resolved_expand

        super().__init__(
            content=content_with_glow,
            bgcolor=COLOR_SURFACE,
            border_radius=12,
            border=ft.border.all(1, ft.Colors.with_opacity(0.08, ft.Colors.WHITE)),
            expand=resolved_expand,
            height=resolved_height,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            shadow=get_card_shadow(),
        )
