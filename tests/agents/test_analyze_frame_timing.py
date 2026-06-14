from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "agents" / "scripts" / "analyze_frame_timing.py"


def load_analyzer():
    assert SCRIPT_PATH.exists(), "analyze_frame_timing.py should exist"
    spec = importlib.util.spec_from_file_location("analyze_frame_timing", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_analyze_frame_timing_parses_revision_and_gpu_fields() -> None:
    analyzer = load_analyzer()
    text = """
    frame_timing revision=12 dropped_frames=0 post_submit_gpu_ms=0.21 total_render_gpu_ms=0.54 submit_duration_us=120
    frame_timing revision=13 dropped_frames=1 post_submit_gpu_ms=0.30 total_render_gpu_ms=0.70 submit_duration_us=150
    """.strip()

    summary = analyzer.summarize_frame_timing(analyzer.parse_frame_timing_lines(text.splitlines()))

    assert summary["timing_rows"] == 2
    assert summary["revisions_seen"] == [12, 13]
    assert summary["dropped_frames_total"] == 1
    assert summary["submit_duration_us_min"] == 120
    assert summary["submit_duration_us_max"] == 150
    assert summary["submit_duration_us_mean"] == pytest.approx(135.0)
    assert summary["post_submit_gpu_ms_mean"] == pytest.approx(0.255)
    assert summary["total_render_gpu_ms_mean"] == pytest.approx(0.62)


def test_analyze_frame_timing_zero_rows_exit_success_summary() -> None:
    analyzer = load_analyzer()

    summary = analyzer.summarize_frame_timing(
        analyzer.parse_frame_timing_lines(["overlay_ready_sent", "not a timing row"])
    )

    assert summary["timing_rows"] == 0
    assert summary["revisions_seen"] == []
    assert summary["dropped_frames_total"] == 0
    assert summary["submit_duration_us_min"] is None
    assert summary["submit_duration_us_max"] is None
    assert summary["submit_duration_us_mean"] is None


def test_analyze_frame_timing_cli_json(tmp_path: Path) -> None:
    load_analyzer()
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "frame_timing revision=21 dropped_frames=2 post_submit_gpu_ms=none "
        "total_render_gpu_ms=1.25 submit_duration_us=200\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(log_path), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(result.stdout)
    assert summary["timing_rows"] == 1
    assert summary["revisions_seen"] == [21]
    assert summary["dropped_frames_total"] == 2
    assert summary["submit_duration_us_mean"] == 200
    assert "post_submit_gpu_ms_mean" not in summary
    assert summary["total_render_gpu_ms_mean"] == 1.25


def test_analyze_frame_timing_cli_summary(tmp_path: Path) -> None:
    load_analyzer()
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "frame_timing revision=42 dropped_frames=0 post_submit_gpu_ms=0.50 "
        "total_render_gpu_ms=0.75 submit_duration_us=99\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(log_path), "--summary"],
        check=True,
        capture_output=True,
        text=True,
    )

    lines = result.stdout.splitlines()
    assert "timing_rows=1" in lines
    assert "revisions_seen=42" in lines
    assert "dropped_frames_total=0" in lines
    assert "submit_duration_us_min=99" in lines
    assert "submit_duration_us_max=99" in lines
    assert "submit_duration_us_mean=99" in lines
    assert "post_submit_gpu_ms_mean=0.5" in lines
    assert "total_render_gpu_ms_mean=0.75" in lines


def test_analyze_frame_timing_ignores_malformed_lines() -> None:
    analyzer = load_analyzer()
    text = """
    frame_timing revision=oops dropped_frames=bad post_submit_gpu_ms=0.1 total_render_gpu_ms=0.2 submit_duration_us=30
    frame_timing revision=31 dropped_frames=0 post_submit_gpu_ms=0.10 total_render_gpu_ms=0.20 submit_duration_us=30
    frame_timing revision=32 dropped_frames=1 post_submit_gpu_ms=broken total_render_gpu_ms=0.40 submit_duration_us=60
    """.strip()

    summary = analyzer.summarize_frame_timing(analyzer.parse_frame_timing_lines(text.splitlines()))

    assert summary["timing_rows"] == 1
    assert summary["revisions_seen"] == [31]
    assert summary["dropped_frames_total"] == 0
    assert summary["submit_duration_us_mean"] == 30
    assert summary["post_submit_gpu_ms_mean"] == 0.10
    assert summary["total_render_gpu_ms_mean"] == 0.20


def test_analyze_frame_timing_requires_gpu_fields_but_allows_explicit_none() -> None:
    analyzer = load_analyzer()
    text = """
    frame_timing revision=41 dropped_frames=0 submit_duration_us=10
    frame_timing revision=42 dropped_frames=0 post_submit_gpu_ms=none total_render_gpu_ms=none submit_duration_us=20
    """.strip()

    summary = analyzer.summarize_frame_timing(analyzer.parse_frame_timing_lines(text.splitlines()))

    assert summary["timing_rows"] == 1
    assert summary["revisions_seen"] == [42]
    assert summary["submit_duration_us_min"] == 20
    assert summary["submit_duration_us_max"] == 20
    assert summary["submit_duration_us_mean"] == 20
    assert "post_submit_gpu_ms_mean" not in summary
    assert "total_render_gpu_ms_mean" not in summary


def test_analyze_frame_timing_requires_submit_duration_field_but_allows_explicit_none() -> None:
    analyzer = load_analyzer()
    text = """
    frame_timing revision=51 dropped_frames=0 post_submit_gpu_ms=0.10 total_render_gpu_ms=0.20
    frame_timing revision=52 dropped_frames=0 post_submit_gpu_ms=0.20 total_render_gpu_ms=0.30 submit_duration_us=oops
    frame_timing revision=53 dropped_frames=0 post_submit_gpu_ms=0.30 total_render_gpu_ms=0.40 submit_duration_us=none
    """.strip()

    summary = analyzer.summarize_frame_timing(analyzer.parse_frame_timing_lines(text.splitlines()))

    assert summary["timing_rows"] == 1
    assert summary["revisions_seen"] == [53]
    assert summary["submit_duration_us_min"] is None
    assert summary["submit_duration_us_max"] is None
    assert summary["submit_duration_us_mean"] is None
    assert summary["post_submit_gpu_ms_mean"] == 0.30
    assert summary["total_render_gpu_ms_mean"] == 0.40


def test_analyze_frame_timing_uses_frame_submitted_without_timing_rows() -> None:
    analyzer = load_analyzer()
    text = """
    frame_submitted revision=7 visible_block_count=1 self_block_count=0 fully_transparent=false overlay_visible_before=false overlay_visible_after=true should_show_after_submit=true submit_duration_us=321
    frame_submitted revision=8 visible_block_count=1 self_block_count=0 fully_transparent=false overlay_visible_before=true overlay_visible_after=true should_show_after_submit=false
    """.strip()

    summary = analyzer.summarize_frame_timing(analyzer.parse_frame_timing_lines(text.splitlines()))

    assert summary["timing_rows"] == 0
    assert summary["revisions_seen"] == [7, 8]
    assert summary["dropped_frames_total"] == 0
    assert summary["submit_duration_us_min"] == 321
    assert summary["submit_duration_us_max"] == 321
    assert summary["submit_duration_us_mean"] == 321
