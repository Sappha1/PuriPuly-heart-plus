from __future__ import annotations

from uuid import uuid4

from puripuly_heart.core.overlay.protocol import OverlayPresentationBlock
from puripuly_heart.core.overlay.sink import OverlayEventAdapter
from puripuly_heart.domain.models import Transcript


def _peer(name: str = "", cluster: int = -1) -> Transcript:
    return Transcript(utterance_id=uuid4(), text="hi", is_final=True, created_at=0.0,
                      channel="peer", speaker_name=name, speaker_cluster_id=cluster)


def test_each_identified_voice_keeps_its_own_slot() -> None:
    adapter = OverlayEventAdapter()
    a = adapter.transcript_final(_peer("Alex"), source_language="ja", target_language="en")
    b = adapter.transcript_final(_peer("Robin"), source_language="ja", target_language="en")
    a2 = adapter.transcript_final(_peer("Alex"), source_language="ja", target_language="en")
    c = adapter.transcript_final(_peer(cluster=7), source_language="ja", target_language="en")
    unknown = adapter.transcript_final(_peer(), source_language="ja", target_language="en")

    assert (a.speaker_index, b.speaker_index, a2.speaker_index, c.speaker_index) == (0, 1, 0, 2)
    assert unknown.speaker_index is None


def test_slots_wrap_after_four_voices() -> None:
    adapter = OverlayEventAdapter()
    slots = [adapter.speaker_index_for(_peer(f"V{i}")) for i in range(6)]
    assert slots == [0, 1, 2, 3, 0, 1]


def _block(**kw) -> OverlayPresentationBlock:
    base = dict(id="peer:x", occupant_key="peer:x", appearance_seq=1, channel="peer",
                block_variant="finalized", primary_text="hello", secondary_text="こんにちは",
                secondary_enabled=True)
    base.update(kw)
    return OverlayPresentationBlock(**base)


def test_block_carries_the_slot_over_the_wire() -> None:
    block = _block(speaker_index=2)
    assert OverlayPresentationBlock.from_dict(block.to_dict()).speaker_index == 2
    assert "speaker_index" not in _block().to_dict()


def test_renderer_colors_per_voice() -> None:
    from puripuly_heart.ui import desktop_overlay as d

    assert d._peer_colors(_block()) == (d._DESKTOP_CAPTION_PEER_SOURCE, d._DESKTOP_CAPTION_GOLD)
    assert d._peer_colors(_block(speaker_index=0)) == d._DESKTOP_CAPTION_PEER_SPEAKER_COLORS[0]
    second = d._peer_colors(_block(speaker_index=1))
    assert second != d._peer_colors(_block(speaker_index=0))
    lines = d._peer_finalized_lines(_block(speaker_index=1), primary_text="hello",
                                    secondary_text="こんにちは", primary_font_size=20,
                                    secondary_font_size=16)
    assert {line.color for line in lines} == set(second)


def test_presenter_tags_peer_blocks_by_utterance() -> None:
    from puripuly_heart.core.overlay.presenter import OverlayPresenter

    presenter = OverlayPresenter.__new__(OverlayPresenter)
    uid = uuid4()
    presenter._peer_speakers = {uid: 3}
    tagged = presenter._with_speaker(("peer", uid), _block(id=f"peer:{uid}"))
    untouched = presenter._with_speaker(("self", uid), _block(channel="self", block_variant="finalized"))

    assert tagged.speaker_index == 3
    assert untouched.speaker_index is None
