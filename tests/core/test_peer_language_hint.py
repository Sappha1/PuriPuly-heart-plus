"""r382: the peer recogniser is never given a language hint.

r378 passed the user's pinned peer language when Auto Detect was off, to give
them a lever after the model's own detection wrote short English ("bye bye",
"hi") in Chinese characters.

It was reverted after it did real damage. In an English-only room with the peer
language left on Chinese, EVERY line was translated into Chinese by the
recogniser and then translated back to English by the translator — the speaker's
own sentence returned to them via a round trip, reading naturally, with nothing
to indicate anything had gone wrong:

    spoken     "I—no—don't know how to close it."
    recognised "我，不，知道怎么close。"          <- translated, not transcribed
    translated "—no—don't know how to close it."  <- back again

This model reads `language` as the language to PRODUCE, not the language to
expect, so any hint that is not what is actually being spoken turns it into a
translator. The lever only helps when the pinned language is right, and nothing
can tell a correct pin from a stale one — the control that sets it is the
dashboard language picker, which people reasonably read as "what I want to
read". A wrong hint fabricates fluent, plausible content; the fault it was
meant to fix was an occasional mis-spelled short word.
"""
from __future__ import annotations

from puripuly_heart.app import wiring
from puripuly_heart.config.settings import AppSettings


def _settings(*, auto_detect: bool, peer_language: str) -> AppSettings:
    s = AppSettings()
    s.provider.peer_stt = wiring.STTProviderName.LOCAL_QWEN
    s.languages.source_language = "en"
    s.languages.peer_source_language = peer_language
    s.languages.auto_detect_peer_voice = auto_detect
    return s


def test_no_hint_is_ever_sent_however_the_peer_language_is_set() -> None:
    """The regression in one assertion: a pinned language must not reach the
    recogniser, because it makes it translate rather than transcribe."""
    for auto_detect in (True, False):
        for peer_language in ("zh-CN", "en", "ja", ""):
            resolved = wiring.resolve_peer_stt_config(
                _settings(auto_detect=auto_detect, peer_language=peer_language)
            )
            assert resolved.language_hint is None, (
                f"auto_detect={auto_detect} peer={peer_language!r} sends "
                f"hint={resolved.language_hint!r}; a hint that is not what is "
                f"actually spoken makes this model translate, so an English "
                f"room with a stale Chinese setting gets every line rewritten"
            )


def test_the_peer_language_still_reaches_everything_else() -> None:
    """Reverting the hint must not detach the setting from the rest of the
    pipeline — it still selects the translation source."""
    resolved = wiring.resolve_peer_stt_config(
        _settings(auto_detect=False, peer_language="ja")
    )
    assert resolved.source_language == "ja"
