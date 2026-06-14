from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DesktopOverlayAutomatedReadinessCheck:
    id: str
    title: str
    command: str
    level: str
    coverage_tags: frozenset[str]
    evidence: str


@dataclass(frozen=True, slots=True)
class DesktopOverlayManualReadinessCheck:
    id: str
    title: str
    qa_surface: str
    evidence_required: bool
    coverage_tags: frozenset[str]
    evidence: str


@dataclass(frozen=True, slots=True)
class DesktopOverlayEvidencePolicy:
    logs_dir: str
    recommended_note_path: str
    required_sections: tuple[str, ...]
    outcome_values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DesktopOverlayL3EscalationTrigger:
    id: str
    title: str
    required_level: str
    evidence: str


@dataclass(frozen=True, slots=True)
class DesktopOverlayReadinessChecklist:
    minimum_level: str
    preview_command: str
    automated_checks: tuple[DesktopOverlayAutomatedReadinessCheck, ...]
    manual_checks: tuple[DesktopOverlayManualReadinessCheck, ...]
    evidence_policy: DesktopOverlayEvidencePolicy
    l3_escalation_triggers: tuple[DesktopOverlayL3EscalationTrigger, ...]
    forbidden_preview_calls: tuple[str, ...]
    replaces_terminal_gate: bool
    terminal_gate_order: int
    rust_rebuild_invariant: str


_PYTHON = r".venv\Scripts\python.exe"
_PREVIEW_COMMAND = f"{_PYTHON} -m puripuly_heart.ui.desktop_overlay --preview"


def desktop_overlay_readiness_checklist() -> DesktopOverlayReadinessChecklist:
    """Return the reviewable readiness evidence expected before Order 15 certification."""

    return DesktopOverlayReadinessChecklist(
        minimum_level="L2",
        preview_command=_PREVIEW_COMMAND,
        automated_checks=_automated_checks(),
        manual_checks=_manual_checks(),
        evidence_policy=DesktopOverlayEvidencePolicy(
            logs_dir="agents/logs",
            recommended_note_path=(
                "agents/logs/2026-05-19-flet-desktop-overlay-order-14-readiness.md"
            ),
            required_sections=(
                "verification_level",
                "commands",
                "outcome",
                "skipped_items",
                "assumptions",
            ),
            outcome_values=("PASS", "FAIL"),
        ),
        l3_escalation_triggers=_l3_escalation_triggers(),
        forbidden_preview_calls=(
            "providers",
            "brokers",
            "STT",
            "translation",
            "SecretStore",
            "settings-save",
        ),
        replaces_terminal_gate=False,
        terminal_gate_order=15,
        rust_rebuild_invariant=(
            "If Rust code is touched unexpectedly, rebuild the Windows Rust overlay as the final step."
        ),
    )


def _automated_checks() -> tuple[DesktopOverlayAutomatedReadinessCheck, ...]:
    return (
        DesktopOverlayAutomatedReadinessCheck(
            id="preview_fixture_availability",
            title="Preview fixtures are available and complete",
            command=f'{_PYTHON} -m pytest tests -k "desktop_overlay and preview and fixtures"',
            level="L2",
            coverage_tags=frozenset({"fixture_availability", "assembled_renderer_readiness"}),
            evidence="Records local multilingual, role/state, wrapping, and background fixture tests.",
        ),
        DesktopOverlayAutomatedReadinessCheck(
            id="preview_i18n_coverage",
            title="Preview and desktop overlay i18n coverage is complete",
            command=f'{_PYTHON} -m pytest tests -k "desktop_overlay and preview and i18n"',
            level="L2",
            coverage_tags=frozenset({"i18n_coverage", "fixture_availability"}),
            evidence="Records locale-bundle coverage for preview labels and desktop overlay strings.",
        ),
        DesktopOverlayAutomatedReadinessCheck(
            id="preview_safety_import_guards",
            title="Preview startup stays local-only and lightweight",
            command=(
                f"{_PYTHON} -m pytest tests -k "
                '"desktop_overlay and preview and (guard or import or secret or settings_save)"'
            ),
            level="L2",
            coverage_tags=frozenset({"preview_safety", "import_guards"}),
            evidence=(
                "Records provider, broker, STT, translation, SecretStore, settings-save, "
                "and secret-fixture guard outcomes."
            ),
        ),
        DesktopOverlayAutomatedReadinessCheck(
            id="packaging_fixture_data_readiness",
            title="Preview fixture data is packaging-ready or explicitly embedded",
            command=f'{_PYTHON} -m pytest tests -k "desktop_overlay and (packaging or fixture_data)"',
            level="L2",
            coverage_tags=frozenset({"packaging_readiness", "fixture_availability"}),
            evidence="Records whether package data or hidden imports are needed for preview fixtures.",
        ),
        DesktopOverlayAutomatedReadinessCheck(
            id="assembled_controller_readiness",
            title="Controller routing, mode, bounds, and persistence surfaces are ready",
            command=(
                f"{_PYTHON} -m pytest tests -k "
                '"desktop and (target or bounds or interaction_mode or reset or gui)"'
            ),
            level="L2",
            coverage_tags=frozenset({"assembled_controller_readiness"}),
            evidence=(
                "Records target routing, edit/locked mode control, bounds/reset handling, "
                "and GUI state outcomes."
            ),
        ),
        DesktopOverlayAutomatedReadinessCheck(
            id="assembled_renderer_readiness",
            title="Renderer lifecycle, caption, window, and pass-through surfaces are ready",
            command=(
                f"{_PYTHON} -m pytest tests -k "
                '"desktop_overlay and (manifest or bridge or lifecycle or snapshot_mapping or '
                'caption_rendering or window_mode or bounds or flet_compat or shutdown)"'
            ),
            level="L2",
            coverage_tags=frozenset({"assembled_renderer_readiness"}),
            evidence="Records renderer bootstrap, mapping, rendering, window, bounds, and shutdown tests.",
        ),
        DesktopOverlayAutomatedReadinessCheck(
            id="readiness_policy_self_check",
            title="Readiness checklist remains test-covered and non-terminal",
            command=f'{_PYTHON} -m pytest tests -k "desktop_overlay and readiness"',
            level="L2",
            coverage_tags=frozenset({"preview_safety", "packaging_readiness"}),
            evidence="Records that Order 14 defines readiness evidence without replacing Order 15.",
        ),
    )


def _manual_checks() -> tuple[DesktopOverlayManualReadinessCheck, ...]:
    return (
        _manual_check(
            "transparent_background",
            "Transparent window has no unwanted opaque background",
            "visual_readability",
        ),
        _manual_check("always_on_top", "Overlay remains above normal desktop windows", "window"),
        _manual_check("default_edit_mode", "Overlay starts in edit mode by default", "mode"),
        _manual_check("drag_affordance", "Drag works through the edit affordance", "window"),
        _manual_check(
            "native_resize_where_available",
            "OS/native resize works where Flet and the platform expose it",
            "window",
        ),
        _manual_check(
            "pass_through_click_through",
            "Locking hides edit chrome and lets clicks reach windows underneath",
            "mode",
        ),
        _manual_check(
            "return_to_edit_from_main_gui",
            "Main GUI can return locked desktop captions to edit mode",
            "mode",
        ),
        _manual_check(
            "cjk_emoji_readability",
            "Korean, Japanese, Chinese, English, mixed-script, and emoji remain readable",
            "visual_readability",
        ),
        _manual_check("long_wrapping", "Long CJK and English captions wrap readably", "text"),
        _manual_check(
            "no_caption_edit_placeholder",
            "No-caption edit mode shows a visible positioning placeholder",
            "no_caption_state",
        ),
        _manual_check(
            "no_caption_pass_through_transparent",
            "No-caption locked mode is fully transparent",
            "no_caption_state",
        ),
        _manual_check(
            "contrast_bright_dark_busy",
            "Default, low, and high background-alpha presets work on bright, dark, and busy surfaces",
            "visual_readability",
        ),
        _manual_check(
            "no_external_provider_secret_calls",
            "Preview QA makes no provider, broker, STT, translation, SecretStore, or settings-save calls",
            "preview_safety",
        ),
    )


def _manual_check(
    check_id: str,
    title: str,
    coverage_tag: str,
) -> DesktopOverlayManualReadinessCheck:
    return DesktopOverlayManualReadinessCheck(
        id=check_id,
        title=title,
        qa_surface=(
            f"preview command: {_PREVIEW_COMMAND} using the real transparent "
            "FletDesktopRendererWindow surface, local fixture/preset controls, and "
            "preview keyboard E/Esc return-to-edit after locking"
        ),
        evidence_required=True,
        coverage_tags=frozenset({coverage_tag}),
        evidence="Record PASS/FAIL, skipped items, screenshots or observation notes, and assumptions.",
    )


def _l3_escalation_triggers() -> tuple[DesktopOverlayL3EscalationTrigger, ...]:
    return (
        _l3_trigger("packaging_configuration_changed", "Packaging configuration changed"),
        _l3_trigger("build_configuration_changed", "Build configuration changed"),
        _l3_trigger("installer_configuration_changed", "Installer configuration changed"),
        _l3_trigger("release_configuration_changed", "Release configuration changed"),
    )


def _l3_trigger(trigger_id: str, title: str) -> DesktopOverlayL3EscalationTrigger:
    return DesktopOverlayL3EscalationTrigger(
        id=trigger_id,
        title=title,
        required_level="L3",
        evidence="Run or explicitly skip Windows build/installer verification with environment reason.",
    )
