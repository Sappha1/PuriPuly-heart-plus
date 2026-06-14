from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_EVENT_RE = re.compile(
    r"\b(?P<event>frame_timing|openvr_frame_timing|frame_submitted)\b(?P<body>.*)$"
)
_KV_RE = re.compile(r"(?P<key>[A-Za-z0-9_]+)=(?P<value>\[[^\]]*\]|\S+)")
_MISSING = object()

_SUMMARY_FIELD_ORDER = [
    "timing_rows",
    "revisions_seen",
    "dropped_frames_total",
    "submit_duration_us_min",
    "submit_duration_us_max",
    "submit_duration_us_mean",
    "post_submit_gpu_ms_mean",
    "total_render_gpu_ms_mean",
]


@dataclass(frozen=True)
class FrameTimingRecord:
    source: str
    revision: int | None
    dropped_frames: int | None = None
    post_submit_gpu_ms: float | None = None
    total_render_gpu_ms: float | None = None
    submit_duration_us: int | None = None

    @property
    def is_timing_row(self) -> bool:
        return self.source in {"frame_timing", "openvr_frame_timing"}


def parse_frame_timing_lines(lines: Iterable[str]) -> list[FrameTimingRecord]:
    records: list[FrameTimingRecord] = []
    last_submitted_revision: int | None = None
    last_submitted_duration_us: int | None = None

    for line in lines:
        match = _EVENT_RE.search(line)
        if match is None:
            continue
        event = match.group("event")
        fields = _parse_key_values(match.group("body"))

        if event == "frame_submitted":
            record = _parse_frame_submitted(fields)
            if record is None:
                continue
            last_submitted_revision = record.revision
            last_submitted_duration_us = record.submit_duration_us
            records.append(record)
            continue

        if event == "frame_timing":
            record = _parse_canonical_frame_timing(fields)
        else:
            record = _parse_legacy_openvr_frame_timing(
                fields,
                last_submitted_revision=last_submitted_revision,
                last_submitted_duration_us=last_submitted_duration_us,
            )
        if record is not None:
            records.append(record)

    return records


def summarize_frame_timing(records: Iterable[FrameTimingRecord]) -> dict[str, object]:
    record_list = list(records)
    timing_records = [record for record in record_list if record.is_timing_row]
    revisions_seen = sorted(
        {record.revision for record in record_list if record.revision is not None}
    )

    durations_by_revision: dict[int, int] = {}
    unrevisioned_durations: list[int] = []
    for record in record_list:
        if record.submit_duration_us is None:
            continue
        if record.revision is None:
            unrevisioned_durations.append(record.submit_duration_us)
            continue
        if record.revision not in durations_by_revision:
            durations_by_revision[record.revision] = record.submit_duration_us

    for record in timing_records:
        if record.submit_duration_us is None:
            continue
        if record.revision is None:
            continue
        durations_by_revision[record.revision] = record.submit_duration_us

    submit_durations = [*durations_by_revision.values(), *unrevisioned_durations]
    post_submit_gpu_values = [
        record.post_submit_gpu_ms
        for record in timing_records
        if record.post_submit_gpu_ms is not None
    ]
    total_render_gpu_values = [
        record.total_render_gpu_ms
        for record in timing_records
        if record.total_render_gpu_ms is not None
    ]

    summary: dict[str, object] = {
        "timing_rows": len(timing_records),
        "revisions_seen": revisions_seen,
        "dropped_frames_total": sum(record.dropped_frames or 0 for record in timing_records),
        "submit_duration_us_min": min(submit_durations) if submit_durations else None,
        "submit_duration_us_max": max(submit_durations) if submit_durations else None,
        "submit_duration_us_mean": _mean(submit_durations) if submit_durations else None,
    }
    if post_submit_gpu_values:
        summary["post_submit_gpu_ms_mean"] = _mean(post_submit_gpu_values)
    if total_render_gpu_values:
        summary["total_render_gpu_ms_mean"] = _mean(total_render_gpu_values)
    return summary


def render_summary(summary: dict[str, object]) -> str:
    lines = []
    for field in _SUMMARY_FIELD_ORDER:
        if field not in summary:
            continue
        lines.append(f"{field}={_format_summary_value(summary[field])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize passive native overlay frame timing diagnostics."
    )
    parser.add_argument("overlay_log", type=Path)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--json", action="store_true", help="emit a JSON summary")
    output.add_argument("--summary", action="store_true", help="emit key=value summary lines")
    args = parser.parse_args(argv)

    text = args.overlay_log.read_text(encoding="utf-8", errors="replace")
    summary = summarize_frame_timing(parse_frame_timing_lines(text.splitlines()))
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(render_summary(summary))
    return 0


def _parse_key_values(body: str) -> dict[str, str]:
    return {match.group("key"): match.group("value") for match in _KV_RE.finditer(body)}


def _parse_frame_submitted(fields: dict[str, str]) -> FrameTimingRecord | None:
    revision = _parse_optional_int(fields.get("revision"))
    if revision is None:
        return None
    submit_duration_us = _parse_optional_int(fields.get("submit_duration_us"))
    return FrameTimingRecord(
        source="frame_submitted",
        revision=revision,
        submit_duration_us=submit_duration_us,
    )


def _parse_canonical_frame_timing(fields: dict[str, str]) -> FrameTimingRecord | None:
    revision = _parse_optional_int(fields.get("revision"))
    dropped_frames = _parse_optional_int(fields.get("dropped_frames"))
    post_submit_gpu_ms = _parse_float_field(fields, "post_submit_gpu_ms")
    total_render_gpu_ms = _parse_float_field(fields, "total_render_gpu_ms")
    submit_duration_us = _parse_required_optional_int(fields, "submit_duration_us")
    if (
        revision is None
        or dropped_frames is None
        or post_submit_gpu_ms is _MISSING
        or total_render_gpu_ms is _MISSING
        or submit_duration_us is _MISSING
    ):
        return None
    return FrameTimingRecord(
        source="frame_timing",
        revision=revision,
        dropped_frames=dropped_frames,
        post_submit_gpu_ms=post_submit_gpu_ms,
        total_render_gpu_ms=total_render_gpu_ms,
        submit_duration_us=submit_duration_us,
    )


def _parse_legacy_openvr_frame_timing(
    fields: dict[str, str],
    *,
    last_submitted_revision: int | None,
    last_submitted_duration_us: int | None,
) -> FrameTimingRecord | None:
    dropped_frames = _parse_optional_int(fields.get("num_dropped_frames"))
    post_submit_gpu_ms = _parse_float_field(fields, "post_submit_gpu_ms")
    total_render_gpu_ms = _parse_float_field(fields, "total_render_gpu_ms")
    if dropped_frames is None or post_submit_gpu_ms is _MISSING or total_render_gpu_ms is _MISSING:
        return None
    return FrameTimingRecord(
        source="openvr_frame_timing",
        revision=last_submitted_revision,
        dropped_frames=dropped_frames,
        post_submit_gpu_ms=post_submit_gpu_ms,
        total_render_gpu_ms=total_render_gpu_ms,
        submit_duration_us=last_submitted_duration_us,
    )


def _parse_optional_int(value: str | None) -> int | None:
    if value is None or value.lower() == "none":
        return None
    if not re.fullmatch(r"\d+", value):
        return None
    return int(value)


def _parse_required_optional_int(fields: dict[str, str], key: str) -> int | None | object:
    value = fields.get(key)
    if value is None:
        return _MISSING
    if value.lower() == "none":
        return None
    if not re.fullmatch(r"\d+", value):
        return _MISSING
    return int(value)


def _parse_float_field(fields: dict[str, str], key: str) -> float | None | object:
    value = fields.get(key)
    if value is None:
        return _MISSING
    if value.lower() == "none":
        return None
    try:
        return float(value)
    except ValueError:
        return _MISSING


def _mean(values: list[int] | list[float]) -> float:
    return sum(values) / len(values)


def _format_summary_value(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, list):
        return ",".join(str(item) for item in value) if value else "none"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
