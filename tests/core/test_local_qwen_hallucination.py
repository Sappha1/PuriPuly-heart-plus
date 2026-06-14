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


def test_known_local_qwen_hallucination_set_contains_only_selected_artifacts() -> None:
    module = _load_detector_module()

    assert module.KNOWN_LOCAL_QWEN_HALLUCINATIONS == frozenset({"leşme", "acia"})


@pytest.mark.parametrize("text", ["leşme", "acia", "  leşme  ", "\tacia\r\n"])
def test_known_local_qwen_hallucination_detector_accepts_exact_artifacts_after_strip(
    text: str,
) -> None:
    module = _load_detector_module()

    assert module.is_known_local_qwen_hallucination(text) is True


@pytest.mark.parametrize("text", ["的答案", "虚构", "夫", "夫夫", "格力"])
def test_known_local_qwen_hallucination_detector_rejects_chinese_looking_exclusions(
    text: str,
) -> None:
    module = _load_detector_module()

    assert module.is_known_local_qwen_hallucination(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
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
