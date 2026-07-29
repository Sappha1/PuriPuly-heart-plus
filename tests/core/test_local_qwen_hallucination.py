from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

MODULE_NAME = "puripuly_heart.core.stt.local_qwen_hallucination"


def _load_detector_module():
    try:
        return importlib.import_module(MODULE_NAME)
    except ModuleNotFoundError as exc:
        if exc.name == MODULE_NAME:
            pytest.fail(f"Detector module is missing: {MODULE_NAME}")
        raise


def test_known_local_qwen_hallucination_set_contains_core_artifacts() -> None:
    # r307/r312 expanded the set from field data (two users' logs); assert the
    # core artifacts are present rather than pinning the exact contents.
    module = _load_detector_module()

    assert {"leşme", "acia", "的答案是", "虚构", "格力空调"} <= set(
        module.KNOWN_LOCAL_QWEN_HALLUCINATIONS
    )


@pytest.mark.parametrize("text", ["leşme", "acia", "  leşme  ", "\tacia\r\n"])
def test_known_local_qwen_hallucination_detector_accepts_exact_artifacts_after_strip(
    text: str,
) -> None:
    module = _load_detector_module()

    assert module.is_known_local_qwen_hallucination(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "夫夫",
        # Stock fragments EMBEDDED in real sentences must never match — only
        # bare whole-utterance junk does.
        "这是我的答案是什么意思呢朋友",
        "我家的格力空调坏了要修一下才行呢",
        "他喜欢虚构小说和故事",
    ],
)
def test_known_local_qwen_hallucination_detector_rejects_real_speech(
    text: str,
) -> None:
    module = _load_detector_module()

    assert module.is_known_local_qwen_hallucination(text) is False


@pytest.mark.parametrize(
    "text",
    [
        # r312 field data (Anhui user): mic noise emitted these verbatim.
        "的答案是：1000",
        "格力空调，格力空调。",
        "虚构一个故事",
        "合并成一个句子。",
        chr(10).join(f"# {i}" for i in range(2, 28)),  # one-utterance number wall
        "夫",   # single char = junk since r298
        "",
        "   ",
    ],
)
def test_known_local_qwen_hallucination_detector_accepts_field_junk(
    text: str,
) -> None:
    module = _load_detector_module()

    assert module.is_known_local_qwen_hallucination(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # NB: "" / "   " moved to the junk side — trivial output has been
        # suppressed since the r298 single-char rule.
        "Leşme",
        "LEŞME",
        "lesme",
        "leşm",
        "leşmeler",
        "xleşmex",
        "AcIa",
        "acíá",
        "aci",
        "acia.",
        "xaciax",
    ],
)
def test_known_local_qwen_hallucination_detector_rejects_partial_case_and_fuzzy_variants(
    text: str,
) -> None:
    module = _load_detector_module()

    assert module.is_known_local_qwen_hallucination(text) is False


def test_known_local_qwen_hallucination_detector_has_no_ui_settings_provider_or_flet_imports() -> (
    None
):
    module = _load_detector_module()
    module_path = Path(module.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)

    disallowed_imports = (
        "flet",
        "puripuly_heart.ui",
        "puripuly_heart.config",
        "puripuly_heart.providers",
    )
    assert not [
        imported
        for imported in imports
        if any(
            imported == disallowed or imported.startswith(f"{disallowed}.")
            for disallowed in disallowed_imports
        )
    ]
