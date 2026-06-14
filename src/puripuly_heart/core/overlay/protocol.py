from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ChannelId = Literal["self", "peer"]
# `active_peer` remains a reserved compatibility/fallback variant. Normal
# product peer rows are primary-visible only after translation arrival.
BlockVariant = Literal["active_self", "active_peer", "finalized"]


@dataclass(frozen=True, slots=True)
class OverlayPresentationCalibration:
    anchor: str = "head_locked"
    offset_x: float = 0.0
    offset_y: float = -0.45
    distance: float = 1.1
    text_scale: float = 1.0
    background_alpha: float = 0.24

    def to_dict(self) -> dict[str, object]:
        return {
            "anchor": self.anchor,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "distance": self.distance,
            "text_scale": self.text_scale,
            "background_alpha": self.background_alpha,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "OverlayPresentationCalibration":
        return cls(
            anchor=str(data.get("anchor", "head_locked")),
            offset_x=float(data.get("offset_x", 0.0)),
            offset_y=float(data.get("offset_y", -0.45)),
            distance=float(data.get("distance", 1.1)),
            text_scale=float(data.get("text_scale", 1.0)),
            background_alpha=float(data.get("background_alpha", 0.24)),
        )


@dataclass(frozen=True, slots=True)
class OverlayPresentationBlock:
    id: str
    occupant_key: str
    appearance_seq: int
    channel: ChannelId
    block_variant: BlockVariant
    primary_text: str
    secondary_text: str
    secondary_enabled: bool
    primary_language: str | None = None
    secondary_language: str | None = None
    update_id: str | None = None
    origin_wall_clock_ms: int | None = None
    session_scope: str | None = None
    source_text_hash: str | None = None
    source_text_len: int | None = None
    logical_turn_key: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "occupant_key": self.occupant_key,
            "appearance_seq": self.appearance_seq,
            "channel": self.channel,
            "block_variant": self.block_variant,
            "primary_text": self.primary_text,
            "secondary_text": self.secondary_text,
            "secondary_enabled": self.secondary_enabled,
        }
        if self.update_id is not None:
            payload["update_id"] = self.update_id
        if self.primary_language is not None:
            payload["primary_language"] = self.primary_language
        if self.secondary_language is not None:
            payload["secondary_language"] = self.secondary_language
        if self.origin_wall_clock_ms is not None:
            payload["origin_wall_clock_ms"] = self.origin_wall_clock_ms
        if self.session_scope is not None:
            payload["session_scope"] = self.session_scope
        if self.source_text_hash is not None:
            payload["source_text_hash"] = self.source_text_hash
        if self.source_text_len is not None:
            payload["source_text_len"] = self.source_text_len
        if self.logical_turn_key is not None:
            payload["logical_turn_key"] = self.logical_turn_key
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "OverlayPresentationBlock":
        if not isinstance(data, dict):
            raise ValueError("overlay presentation block must be an object")
        channel = data.get("channel")
        if channel not in ("self", "peer"):
            raise ValueError(f"invalid overlay presentation channel: {channel!r}")
        block_variant = data.get("block_variant")
        if block_variant not in ("active_self", "active_peer", "finalized"):
            raise ValueError(f"invalid overlay presentation block variant: {block_variant!r}")
        if block_variant == "active_self" and channel != "self":
            raise ValueError("active_self blocks require channel='self'")
        if block_variant == "active_peer" and channel != "peer":
            raise ValueError("active_peer blocks require channel='peer'")
        occupant_key = _require_string_field(data, "occupant_key").strip()
        if not occupant_key:
            raise ValueError("occupant_key must be a non-empty string")
        appearance_seq = _require_non_negative_int_field(data, "appearance_seq")
        return cls(
            id=_require_string_field(data, "id"),
            occupant_key=occupant_key,
            appearance_seq=appearance_seq,
            channel=channel,
            block_variant=block_variant,
            primary_text=_require_string_field(data, "primary_text"),
            secondary_text=_require_string_field(data, "secondary_text"),
            secondary_enabled=_require_bool_field(data, "secondary_enabled"),
            primary_language=_optional_string_field(data, "primary_language"),
            secondary_language=_optional_string_field(data, "secondary_language"),
            update_id=_optional_string_field(data, "update_id"),
            origin_wall_clock_ms=_optional_int_field(data, "origin_wall_clock_ms"),
            session_scope=_optional_string_field(data, "session_scope"),
            source_text_hash=_optional_string_field(data, "source_text_hash"),
            source_text_len=_optional_int_field(data, "source_text_len"),
            logical_turn_key=_optional_string_field(data, "logical_turn_key"),
        )


@dataclass(frozen=True, slots=True)
class OverlayPresentationSnapshot:
    revision: int = 0
    calibration: OverlayPresentationCalibration = field(
        default_factory=OverlayPresentationCalibration
    )
    blocks: list[OverlayPresentationBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "calibration": self.calibration.to_dict(),
            "blocks": [block.to_dict() for block in self.blocks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "OverlayPresentationSnapshot":
        calibration = data.get("calibration", {})
        if not isinstance(calibration, dict):
            raise ValueError("overlay presentation snapshot calibration must be an object")

        raw_blocks = data.get("blocks", [])
        if not isinstance(raw_blocks, list):
            raise ValueError("overlay presentation snapshot blocks must be a list")

        blocks: list[OverlayPresentationBlock] = []
        for block in raw_blocks:
            if not isinstance(block, dict):
                raise ValueError(
                    "overlay presentation snapshot blocks must contain only dict items"
                )
            blocks.append(OverlayPresentationBlock.from_dict(block))

        return cls(
            revision=int(data.get("revision", 0)),
            calibration=OverlayPresentationCalibration.from_dict(calibration),
            blocks=blocks,
        )


def _require_string_field(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _require_bool_field(data: dict[str, object], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a bool")
    return value


def _require_non_negative_int_field(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an int")
    if value < 0:
        raise ValueError(f"{key} must be non-negative")
    return value


def _optional_string_field(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_int_field(data: dict[str, object], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an int")
    return value
