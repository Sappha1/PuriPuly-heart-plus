from __future__ import annotations

from puripuly_heart.ui.desktop_overlay_readiness import (
    desktop_overlay_readiness_checklist,
)


def _coverage_tags(items: object) -> set[str]:
    return {tag for item in items for tag in item.coverage_tags}  # type: ignore[union-attr]


def _ids(items: object) -> set[str]:
    return {item.id for item in items}  # type: ignore[union-attr]


def test_desktop_overlay_readiness_automated_l2_baseline_covers_required_surfaces() -> None:
    checklist = desktop_overlay_readiness_checklist()

    assert checklist.minimum_level == "L2"
    assert checklist.replaces_terminal_gate is False
    assert checklist.terminal_gate_order == 15

    automated_tags = _coverage_tags(checklist.automated_checks)
    assert {
        "preview_safety",
        "fixture_availability",
        "i18n_coverage",
        "import_guards",
        "packaging_readiness",
        "assembled_controller_readiness",
        "assembled_renderer_readiness",
    } <= automated_tags
    assert all(check.level == "L2" for check in checklist.automated_checks)
    assert all(
        check.command.startswith(r".venv\Scripts\python.exe")
        for check in checklist.automated_checks
    )
    assert any(
        "desktop_overlay and preview and fixtures" in check.command
        for check in checklist.automated_checks
    )
    assert any(
        "desktop_overlay and preview and i18n" in check.command
        for check in checklist.automated_checks
    )
    assert any(
        "desktop_overlay and preview and (guard or import or secret or settings_save)"
        in check.command
        for check in checklist.automated_checks
    )
    assert any(
        "desktop_overlay and readiness" in check.command for check in checklist.automated_checks
    )


def test_desktop_overlay_readiness_manual_preview_qa_covers_visual_interaction_and_safety() -> None:
    checklist = desktop_overlay_readiness_checklist()

    assert (
        checklist.preview_command
        == r".venv\Scripts\python.exe -m puripuly_heart.ui.desktop_overlay --preview"
    )
    assert {
        "transparent_background",
        "always_on_top",
        "default_edit_mode",
        "drag_affordance",
        "native_resize_where_available",
        "pass_through_click_through",
        "return_to_edit_from_main_gui",
        "cjk_emoji_readability",
        "long_wrapping",
        "no_caption_edit_placeholder",
        "no_caption_pass_through_transparent",
        "contrast_bright_dark_busy",
        "no_external_provider_secret_calls",
    } <= _ids(checklist.manual_checks)
    assert all(check.evidence_required for check in checklist.manual_checks)
    assert all("preview" in check.qa_surface for check in checklist.manual_checks)
    assert all(
        "real transparent FletDesktopRendererWindow" in check.qa_surface
        for check in checklist.manual_checks
    )
    assert {
        "providers",
        "brokers",
        "STT",
        "translation",
        "SecretStore",
        "settings-save",
    } <= set(checklist.forbidden_preview_calls)


def test_desktop_overlay_readiness_evidence_policy_and_l3_triggers_are_explicit() -> None:
    checklist = desktop_overlay_readiness_checklist()

    assert checklist.evidence_policy.logs_dir == "agents/logs"
    assert (
        checklist.evidence_policy.recommended_note_path
        == "agents/logs/2026-05-19-flet-desktop-overlay-order-14-readiness.md"
    )
    assert {"PASS", "FAIL"} <= set(checklist.evidence_policy.outcome_values)
    assert {
        "verification_level",
        "commands",
        "outcome",
        "skipped_items",
        "assumptions",
    } <= set(checklist.evidence_policy.required_sections)

    triggers = {trigger.id: trigger for trigger in checklist.l3_escalation_triggers}
    assert {
        "packaging_configuration_changed",
        "build_configuration_changed",
        "installer_configuration_changed",
        "release_configuration_changed",
    } <= set(triggers)
    assert all(trigger.required_level == "L3" for trigger in triggers.values())
    assert checklist.rust_rebuild_invariant == (
        "If Rust code is touched unexpectedly, rebuild the Windows Rust overlay as the final step."
    )
