from pathlib import Path


def test_translation_prompt_contains_dynamic_policy_contract() -> None:
    text = Path("prompts/translation_prompt.md").read_text(encoding="utf-8")

    assert "${sourceName}" in text
    assert "${targetName}" in text
    assert "${targetLanguageRules}" in text
    assert "${translationExamples}" in text
    assert "* `[self]` means the local user's earlier utterance." in text
    assert "* `[peer]` means one or more other speakers from the peer audio channel." in text
    assert "[others]" not in text


def test_translation_prompt_treats_context_metadata_as_non_literal_hints() -> None:
    text = Path("prompts/translation_prompt.md").read_text(encoding="utf-8")
    assert "speaker hints" in text
    assert "lightweight metadata" in text
    assert "timestamps" in text
    assert "Speaker labels, brackets, timestamps" not in text
    assert "Do not copy speaker labels" not in text
    assert "relative-age markers" not in text
    assert "unless they were literally spoken" not in text
    assert "Do not invent facts from metadata" not in text
    assert "Plain-text legend" not in text
    assert "* `[self]` means the local user's earlier utterance." in text
    assert "* `[peer]` means one or more other speakers from the peer audio channel." in text
