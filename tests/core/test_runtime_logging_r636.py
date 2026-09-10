"""r636 process hygiene: one log file per process, a quiet deepl SDK logger,
no stderr StreamHandler in the windowed build, and main() routing the overlay
renderer subprocess to its own file without the numpy-heavy sysinfo probe."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

import pytest

from puripuly_heart.core import runtime_logging
from puripuly_heart.core.runtime_logging import (
    MAIN_LOG_FILENAME,
    OVERLAY_LOG_FILENAME,
    configure_main_logging,
    default_main_log_file,
)


def _fresh_logger(tag: str) -> logging.Logger:
    logger = logging.getLogger(f"test.runtime_logging.r636.{tag}.{uuid4()}")
    logger.handlers.clear()
    logger.propagate = False
    return logger


def test_default_main_log_file_honours_log_filename(tmp_path) -> None:
    assert default_main_log_file(log_dir=tmp_path) == tmp_path / MAIN_LOG_FILENAME
    assert default_main_log_file(log_dir=tmp_path, log_filename=OVERLAY_LOG_FILENAME) == (
        tmp_path / "puripuly_heart.overlay.log"
    )


@pytest.mark.parametrize(
    ("log_filename", "expected_log", "expected_backup"),
    [
        (None, "puripuly_heart.log", "puripuly_heart.backup.log"),
        (
            OVERLAY_LOG_FILENAME,
            "puripuly_heart.overlay.log",
            "puripuly_heart.overlay.backup.log",
        ),
    ],
)
def test_configure_main_logging_per_process_file_and_backup_names(
    tmp_path, log_filename, expected_log, expected_backup
) -> None:
    root_logger = _fresh_logger("names")
    kwargs = {} if log_filename is None else {"log_filename": log_filename}
    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path, **kwargs)
    try:
        assert sinks.log_file == tmp_path / expected_log
        assert isinstance(sinks.file_handler, RotatingFileHandler)
        assert sinks.file_handler.rotation_filename(str(sinks.log_file) + ".1") == str(
            tmp_path / expected_backup
        )

        sinks.file_handler.stream.write("old log line\n")
        sinks.file_handler.flush()
        sinks.file_handler.doRollover()

        assert (tmp_path / expected_backup).exists()
        assert not (tmp_path / f"{expected_log}.1").exists()
    finally:
        sinks.close()


def test_configure_main_logging_quiets_the_deepl_sdk_logger(tmp_path) -> None:
    deepl_logger = logging.getLogger("deepl")
    previous_level = deepl_logger.level
    deepl_logger.setLevel(logging.NOTSET)
    root_logger = _fresh_logger("deepl")
    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
    try:
        assert deepl_logger.level == logging.WARNING
        assert not deepl_logger.isEnabledFor(logging.INFO)
        assert deepl_logger.isEnabledFor(logging.WARNING)
    finally:
        sinks.close()
        deepl_logger.setLevel(previous_level)


def test_configure_main_logging_skips_stream_handler_without_stderr(tmp_path, monkeypatch) -> None:
    """The frozen windowed build has sys.stderr None: a StreamHandler built on
    it raised into handleError on every record. Nothing may reach handleError,
    and a second configure must reuse the placeholder, not stack another."""
    monkeypatch.setattr(sys, "stderr", None)
    handle_errors: list[logging.LogRecord] = []
    monkeypatch.setattr(
        logging.Handler, "handleError", lambda self, record: handle_errors.append(record)
    )
    root_logger = _fresh_logger("nostderr")

    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
    try:
        stream_handlers = [
            handler
            for handler in root_logger.handlers
            if isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, RotatingFileHandler)
        ]
        assert stream_handlers == []
        assert isinstance(sinks.stream_handler, logging.NullHandler)
        assert sinks.stream_handler in root_logger.handlers

        root_logger.info("no console here")
        assert sinks.file_queue is not None
        sinks.file_queue.join()
        assert handle_errors == []
        assert "no console here" in sinks.log_file.read_text(encoding="utf-8")

        again = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
        try:
            assert again.stream_handler is sinks.stream_handler
            assert [
                handler for handler in root_logger.handlers
                if isinstance(handler, logging.NullHandler)
            ] == [sinks.stream_handler]
        finally:
            again.close()
    finally:
        sinks.close()


def test_configure_main_logging_still_builds_stream_handler_with_stderr(tmp_path) -> None:
    root_logger = _fresh_logger("stderr")
    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
    try:
        assert isinstance(sinks.stream_handler, logging.StreamHandler)
        assert not isinstance(sinks.stream_handler, logging.NullHandler)
    finally:
        sinks.close()


# ── main(): overlay renderer gets its own file, GUI keeps the main one ──────

def _patch_main_for_routing(monkeypatch, tmp_path):
    import puripuly_heart.main as main_module
    from puripuly_heart.core import system_info

    calls: dict[str, object] = {"sysinfo": 0, "log_filename": []}
    real_configure = runtime_logging.configure_main_logging

    def spy_configure(**kwargs):
        calls["log_filename"].append(kwargs.get("log_filename"))
        return real_configure(root_logger=_fresh_logger("main"), log_dir=tmp_path, **kwargs)

    def spy_sysinfo() -> None:
        calls["sysinfo"] += 1

    monkeypatch.setattr(main_module, "configure_main_logging", spy_configure)
    monkeypatch.setattr(system_info, "log_system_info_async", spy_sysinfo)
    monkeypatch.setattr(main_module, "_run_desktop_overlay", lambda config_path: 0)
    monkeypatch.setattr(main_module, "_run_desktop_overlay_preview", lambda: 0)
    monkeypatch.setattr(
        main_module, "_run_gui", lambda config_path, *, debug_ui_preview: 0
    )
    monkeypatch.setattr(main_module, "_acquire_single_instance_lock", lambda: True)
    return main_module, calls


def test_main_routes_overlay_subprocess_to_its_own_log_without_sysinfo(
    monkeypatch, tmp_path
) -> None:
    main_module, calls = _patch_main_for_routing(monkeypatch, tmp_path)

    manifest = tmp_path / "manifest.json"
    assert main_module.main(["run-desktop-overlay", "--config", str(manifest)]) == 0
    assert main_module.main(["run-desktop-overlay-preview"]) == 0

    assert calls["log_filename"] == [OVERLAY_LOG_FILENAME, OVERLAY_LOG_FILENAME]
    assert calls["sysinfo"] == 0
    assert (tmp_path / "puripuly_heart.overlay.log").exists()
    assert not (tmp_path / "puripuly_heart.log").exists()


def test_main_gui_paths_keep_the_main_log_and_log_sysinfo_once(monkeypatch, tmp_path) -> None:
    main_module, calls = _patch_main_for_routing(monkeypatch, tmp_path)
    config = tmp_path / "settings.json"  # absent: first-run defaults, nothing written

    assert main_module.main(["--config", str(config), "run-gui"]) == 0
    assert calls == {"sysinfo": 1, "log_filename": [None]}
    assert main_module.main(["--config", str(config)]) == 0
    assert calls == {"sysinfo": 2, "log_filename": [None, None]}
    assert (tmp_path / "puripuly_heart.log").exists()
    assert not (tmp_path / "puripuly_heart.overlay.log").exists()


def test_importing_main_caps_blas_pools_without_importing_numpy() -> None:
    """A fresh interpreter (this one may already carry numpy and the caps):
    importing main must set both caps and must not itself pull numpy in."""
    import puripuly_heart

    src_dir = Path(puripuly_heart.__file__).resolve().parents[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")
    }
    env["PYTHONPATH"] = str(src_dir)
    code = (
        "import os, sys\n"
        "import puripuly_heart.main\n"
        "print(os.environ.get('OPENBLAS_NUM_THREADS'), "
        "os.environ.get('OMP_NUM_THREADS'), 'numpy' in sys.modules)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=True,
    )
    assert completed.stdout.split() == ["1", "1", "False"]
