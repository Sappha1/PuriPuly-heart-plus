"""Per-language overlay reading lines (r283).

A native reader can hide the reading for their own language while keeping the
others (zh user: no pinyin, still sees Korean romaja). Defaults are locale
aware: the reading for the UI locale's own language starts off.
"""
from __future__ import annotations

from puripuly_heart.config.settings import from_dict, new_settings_for_first_run, to_dict
from puripuly_heart.core.orchestrator.hub import ClientHub
from tests.helpers.fakes import RecordingOscQueue

_ZH = "你好朋友"
_JA = "こんにちは"
_KO = "안녕하세요"


def _hub(**flags) -> ClientHub:
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue())
    for name, value in flags.items():
        setattr(hub, name, value)
    return hub


def test_pinyin_off_keeps_korean_romaja() -> None:
    hub = _hub(overlay_show_pinyin=False)
    assert hub._with_overlay_translit(_ZH, "zh-CN") == _ZH  # no reading line
    ko = hub._with_overlay_translit(_KO, "ko")
    assert "\n" in ko and ko.endswith(_KO)  # romaja + original


def test_each_language_flag_gates_only_its_language() -> None:
    hub = _hub(overlay_show_romaji=False, overlay_show_romaja=False)
    assert hub._with_overlay_translit(_JA, "ja") == _JA
    assert hub._with_overlay_translit(_KO, "ko-KR") == _KO
    zh = hub._with_overlay_translit(_ZH, "zh-CN")
    assert "\n" in zh and zh.endswith(_ZH)  # pinyin still on


def test_master_toggle_still_disables_everything() -> None:
    hub = _hub(overlay_show_romanization=False)
    assert hub._with_overlay_translit(_ZH, "zh-CN") == _ZH
    assert hub._with_overlay_translit(_KO, "ko") == _KO


def test_unknown_language_falls_back_to_script_sniff() -> None:
    hub = _hub(overlay_show_pinyin=False)
    # Language unknown ("" = auto detect entry) — the hanzi script must still
    # map to the pinyin flag, not the latin flag.
    assert hub._with_overlay_translit(_ZH, "") == _ZH
    ko = hub._with_overlay_translit(_KO, "")
    assert "\n" in ko and ko.endswith(_KO)


def test_precomputed_reading_respects_language_flag() -> None:
    hub = _hub(overlay_show_pinyin=False)
    out = hub._with_overlay_translit(_ZH, "zh-CN", precomputed="ni hao peng you")
    assert out == _ZH


def test_first_run_defaults_hide_own_language_reading() -> None:
    zh = new_settings_for_first_run("zh-CN").overlay
    assert (zh.show_pinyin, zh.show_romaji, zh.show_romaja, zh.show_latin) == (
        False,
        True,
        True,
        True,
    )
    ja = new_settings_for_first_run("ja-JP").overlay
    assert (ja.show_pinyin, ja.show_romaji, ja.show_romaja) == (True, False, True)
    ko = new_settings_for_first_run("ko-KR").overlay
    assert (ko.show_pinyin, ko.show_romaji, ko.show_romaja) == (True, True, False)
    en = new_settings_for_first_run("en-US").overlay
    assert (en.show_pinyin, en.show_romaji, en.show_romaja, en.show_latin) == (
        True,
        True,
        True,
        True,
    )


def test_settings_roundtrip_preserves_explicit_choices() -> None:
    settings = new_settings_for_first_run("en-US")
    settings.overlay.show_pinyin = False
    settings.overlay.show_romaja = False
    loaded = from_dict(to_dict(settings))
    assert loaded.overlay.show_pinyin is False
    assert loaded.overlay.show_romaji is True
    assert loaded.overlay.show_romaja is False
    assert loaded.overlay.show_latin is True


def test_old_config_without_keys_gets_locale_aware_default() -> None:
    settings = new_settings_for_first_run("zh-CN")
    data = to_dict(settings)
    for key in ("show_pinyin", "show_romaji", "show_romaja", "show_latin"):
        data["overlay"].pop(key, None)  # simulate a pre-r283 config
    loaded = from_dict(data)
    assert loaded.overlay.show_pinyin is False  # zh UI -> own pinyin hidden
    assert loaded.overlay.show_romaji is True
    assert loaded.overlay.show_romaja is True
