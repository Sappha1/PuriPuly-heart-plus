from __future__ import annotations

import subprocess
from pathlib import Path

from puripuly_heart.config.prompts import load_prompt_for_provider
from puripuly_heart.config.settings import AppSettings, ensure_prompt_defaults

_REPO = Path(__file__).resolve().parents[2]


def _previous_default() -> str:
    # the stock prompt as shipped before the two-person section
    out = subprocess.run(
        ["git", "show", "aeed009:prompts/translation_prompt.md"],
        cwd=_REPO, capture_output=True, text=True, encoding="utf-8",
    )
    return out.stdout


def test_new_default_covers_two_person_conversations() -> None:
    prompt = load_prompt_for_provider("gemini")
    assert "Two-Person Conversations" in prompt
    assert "${targetLanguageRules}" in prompt  # still a template


def test_a_never_edited_saved_prompt_follows_the_new_default() -> None:
    old = _previous_default()
    if not old:
        return  # no git history available (packaged source)
    settings = AppSettings()
    settings.system_prompt = old.replace("\n", "\r\n") + "\n"  # line endings don't matter

    ensure_prompt_defaults(settings)

    assert settings.system_prompt == load_prompt_for_provider("gemini")


def test_a_customized_prompt_is_never_replaced() -> None:
    settings = AppSettings()
    settings.system_prompt = "Translate casually, like a friend."

    ensure_prompt_defaults(settings)

    assert settings.system_prompt == "Translate casually, like a friend."
