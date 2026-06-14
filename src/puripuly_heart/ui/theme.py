import flet as ft

# Dark Theme - VRCT-style
COLOR_BACKGROUND = "#2e2f32"        # Main background
COLOR_SURFACE = "#3a3b3e"           # Card / sidebar surface
COLOR_ON_BACKGROUND = "#e8e8e8"     # Primary text
COLOR_PRIMARY = "#48a495"           # Teal accent
COLOR_ERROR = "#FF5449"
COLOR_SUCCESS = "#66BB6A"
COLOR_WARNING = "#FF8A65"
COLOR_DIVIDER = "#3f4044"           # Subtle dark divider
COLOR_PRIMARY_CONTAINER = "#1a3a36" # Dark teal container
COLOR_ON_PRIMARY_CONTAINER = "#48a495"
COLOR_ON_SURFACE_VARIANT = "#9e9e9e"
COLOR_SURFACE_DIM = "#252628"       # Deeper surface / OFF state

# Additional dark-palette colors
COLOR_SECONDARY = "#6197b4"         # Sent / blue accent
COLOR_TERTIARY = "#a861b4"          # Received / purple accent
COLOR_TRANS_TONAL = "#2a1f2e"       # Dark purple container (OFF state)
COLOR_TRANS_ON = "#a861b4"          # Translation ON color
COLOR_NEUTRAL = "#6e7175"           # Inactive / muted
COLOR_NEUTRAL_DARK = "#c8c8c8"      # Secondary text / labels
COLOR_SURFACE_TONAL = "#45464a"     # Hover state


def get_app_theme(font_family: str | None = None) -> ft.Theme:
    return ft.Theme(
        color_scheme=ft.ColorScheme(
            surface=COLOR_SURFACE,
            on_surface=COLOR_ON_BACKGROUND,
            primary=COLOR_PRIMARY,
            error=COLOR_ERROR,
            outline=COLOR_DIVIDER,
            background=COLOR_BACKGROUND,
            secondary=COLOR_SECONDARY,
            tertiary=COLOR_TERTIARY,
        ),
        font_family=font_family,
        visual_density=ft.VisualDensity.COMPACT,
        page_transitions=ft.PageTransitionsTheme(
            windows=ft.PageTransitionTheme.NONE,
            macos=ft.PageTransitionTheme.NONE,
            linux=ft.PageTransitionTheme.NONE,
        ),
    )


def get_card_shadow() -> ft.BoxShadow:
    return ft.BoxShadow(
        blur_radius=8,
        color=ft.Colors.with_opacity(0.4, "#000000"),
        offset=ft.Offset(0, 2),
        spread_radius=0,
    )
