from __future__ import annotations

import io
import logging
import re
import time
from dataclasses import dataclass
from logging.handlers import QueueHandler, RotatingFileHandler
from uuid import uuid4

from puripuly_heart.core.runtime_logging import (
    SessionLoggingMode,
    SessionRuntimeLoggingService,
    configure_main_logging,
)


@dataclass
class _SharedSinkBundle:
    stream_handler: logging.Handler
    file_handler: logging.Handler
    log_file: object


class _DelayingForwardingHandler(logging.Handler):
    def __init__(self, target: logging.Handler, *, delayed_message: str) -> None:
        super().__init__()
        self._target = target
        self._delayed_message = delayed_message
        self.started = False

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == self._delayed_message:
            self.started = True
            time.sleep(0.2)
        self._target.handle(record)


def _format_with_handler(handler: logging.Handler) -> str:
    record = logging.LogRecord(
        name="test.runtime",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.created = 0.0
    record.msecs = 123.0
    return handler.format(record)


def _wait_for_log_text(log_file, text: str) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if text in log_file.read_text(encoding="utf-8"):
            return
        time.sleep(0.01)
    assert text in log_file.read_text(encoding="utf-8")


def _wait_until(condition) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    assert condition()


def _make_runtime_logging_capture() -> tuple[SessionRuntimeLoggingService, io.StringIO]:
    stream = io.StringIO()
    stream_handler = logging.StreamHandler(stream)

    root_logger = logging.getLogger(f"test.runtime_logging.root.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    session_logger = logging.getLogger(f"test.runtime_logging.session.{uuid4()}")
    session_logger.handlers.clear()
    session_logger.propagate = False

    runtime_logging = SessionRuntimeLoggingService(
        root_logger=root_logger,
        session_logger=session_logger,
        sinks=_SharedSinkBundle(
            stream_handler=stream_handler,
            file_handler=logging.NullHandler(),
            log_file="runtime.log",
        ),
    )
    return runtime_logging, stream


def test_configure_main_logging_formats_new_handlers_with_millisecond_resolution(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.configure.new.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    try:
        formatted_stream = _format_with_handler(sinks.stream_handler)
        formatted_file = _format_with_handler(sinks.file_handler)

        assert re.fullmatch(
            r"\d{2}:\d{2}:\d{2}\.123 \[INFO\] test\.runtime: hello", formatted_stream
        )
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2}\.123 \[INFO\] test\.runtime: hello", formatted_file)
    finally:
        sinks.close()


def test_configure_main_logging_reused_handlers_get_millisecond_resolution_formatter(
    tmp_path,
) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.configure.reused.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    existing_stream = logging.StreamHandler(io.StringIO())
    existing_stream.setFormatter(logging.Formatter("%(message)s"))
    existing_file = RotatingFileHandler(
        tmp_path / "puripuly_heart.log",
        maxBytes=4096,
        backupCount=0,
        encoding="utf-8",
    )
    existing_file.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(existing_stream)
    root_logger.addHandler(existing_file)

    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    try:
        assert sinks.stream_handler is existing_stream
        assert sinks.file_handler is existing_file
        assert re.fullmatch(
            r"\d{2}:\d{2}:\d{2}\.123 \[INFO\] test\.runtime: hello",
            _format_with_handler(existing_stream),
        )
        assert re.fullmatch(
            r"\d{2}:\d{2}:\d{2}\.123 \[INFO\] test\.runtime: hello",
            _format_with_handler(existing_file),
        )
    finally:
        sinks.close()


def test_configure_main_logging_routes_file_writes_through_queue(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.queue.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    try:
        assert isinstance(sinks.file_queue_handler, QueueHandler)
        assert isinstance(sinks.file_handler, RotatingFileHandler)
        assert sinks.file_queue_handler in root_logger.handlers
        assert sinks.file_handler not in root_logger.handlers

        root_logger.info("queued file record")
        _wait_for_log_text(sinks.log_file, "queued file record")
    finally:
        sinks.close()


def test_configure_main_logging_uses_bounded_rotation_policy(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.rotation_policy.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    try:
        assert isinstance(sinks.file_handler, RotatingFileHandler)
        assert sinks.file_handler.maxBytes == 10 * 1024 * 1024
        assert sinks.file_handler.backupCount == 1
    finally:
        sinks.close()


def test_configure_main_logging_names_single_backup_with_log_extension(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.rotation_name.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    try:
        default_backup_name = str(sinks.log_file) + ".1"
        assert sinks.file_handler.rotation_filename(default_backup_name) == str(
            tmp_path / "puripuly_heart.backup.log"
        )

        sinks.file_handler.stream.write("old log line\n")
        sinks.file_handler.flush()
        sinks.file_handler.doRollover()

        assert (tmp_path / "puripuly_heart.backup.log").exists()
        assert not (tmp_path / "puripuly_heart.log.1").exists()
    finally:
        sinks.close()


def test_configure_main_logging_reuses_existing_queue_handler(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.queue.reuse.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    first = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
    second = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    try:
        queue_handlers = [
            handler for handler in root_logger.handlers if isinstance(handler, QueueHandler)
        ]
        assert queue_handlers == [first.file_queue_handler]
        assert second.file_queue_handler is first.file_queue_handler
        assert second.file_handler is first.file_handler
    finally:
        first.close()
        second.close()


def test_configure_main_logging_keeps_shared_queue_alive_until_last_close(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.queue.refcount.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    first = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
    second = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    try:
        assert second.file_queue_handler is first.file_queue_handler

        first.close()
        assert second.file_queue_handler in root_logger.handlers

        root_logger.info("second still writes after first close")
        _wait_for_log_text(second.log_file, "second still writes after first close")

        second.close()
        assert second.file_queue_handler not in root_logger.handlers
    finally:
        first.close()
        second.close()


def test_force_close_shared_queue_releases_even_with_outstanding_refs(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.queue.force_close.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    first = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
    second = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    try:
        assert second.file_queue_handler is first.file_queue_handler

        first.close(force=True)

        assert first.file_queue_handler not in root_logger.handlers
        second.close()
        assert second.file_queue_handler not in root_logger.handlers
    finally:
        first.close()
        second.close()


def test_default_session_logging_services_share_queue_until_last_close(
    tmp_path, monkeypatch
) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.service.refcount.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False
    monkeypatch.setattr("puripuly_heart.core.runtime_logging.user_config_dir", lambda: tmp_path)

    first = SessionRuntimeLoggingService(root_logger=root_logger)
    second = SessionRuntimeLoggingService(root_logger=root_logger)

    try:
        assert second.log_file == first.log_file

        first.close()
        second.emit_basic("service two writes after service one close")
        _wait_for_log_text(second.log_file, "service two writes after service one close")

        second.close()
        queue_handlers = [
            handler for handler in root_logger.handlers if isinstance(handler, QueueHandler)
        ]
        assert queue_handlers == []
    finally:
        first.close()
        second.close()


def test_configure_main_logging_removes_stale_queue_when_log_dir_changes(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.queue.stale.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = configure_main_logging(root_logger=root_logger, log_dir=first_dir)
    root_logger.info("before log dir switch")
    _wait_for_log_text(first.log_file, "before log dir switch")

    second = configure_main_logging(root_logger=root_logger, log_dir=second_dir)

    try:
        assert first.file_queue_handler not in root_logger.handlers
        assert second.file_queue_handler in root_logger.handlers

        root_logger.info("after log dir switch")
        _wait_for_log_text(second.log_file, "after log dir switch")
        assert "after log dir switch" not in first.log_file.read_text(encoding="utf-8")
    finally:
        first.close()
        second.close()


def test_emit_persisted_writes_directly_to_file_handler(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.persisted.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False
    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
    runtime_logging = SessionRuntimeLoggingService(root_logger=root_logger, sinks=sinks)

    try:
        runtime_logging.emit_persisted("persisted critical record")
        assert "persisted critical record" in sinks.log_file.read_text(encoding="utf-8")
    finally:
        runtime_logging.close()
        sinks.close()


def test_emit_persisted_preserves_queued_record_order_before_direct_write(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.persisted.order.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False
    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
    runtime_logging = SessionRuntimeLoggingService(root_logger=root_logger, sinks=sinks)
    delaying_handler = _DelayingForwardingHandler(
        sinks.file_handler,
        delayed_message="basic before persisted",
    )
    assert sinks.file_queue_listener is not None
    sinks.file_queue_listener.handlers = (delaying_handler,)

    try:
        runtime_logging.emit_basic("basic before persisted")
        _wait_until(lambda: delaying_handler.started)

        runtime_logging.emit_persisted("persisted after queued basic")

        _wait_for_log_text(sinks.log_file, "basic before persisted")
        _wait_for_log_text(sinks.log_file, "persisted after queued basic")
        log_lines = sinks.log_file.read_text(encoding="utf-8").splitlines()
        basic_index = next(
            index for index, line in enumerate(log_lines) if "basic before persisted" in line
        )
        persisted_index = next(
            index for index, line in enumerate(log_lines) if "persisted after queued basic" in line
        )
        assert basic_index < persisted_index
    finally:
        runtime_logging.close()
        sinks.close()


def test_configure_main_logging_reconfigures_after_close(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.queue.close_reconfigure.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    first = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)
    first.close()

    second = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    try:
        assert second.file_queue_handler is not first.file_queue_handler
        assert second.file_queue_handler in root_logger.handlers
        root_logger.info("after reconfigure")
        _wait_for_log_text(second.log_file, "after reconfigure")
    finally:
        second.close()


def test_emit_detailed_lazy_checks_mode_before_formatting() -> None:
    runtime_logging, stream = _make_runtime_logging_capture()
    builder_calls = 0

    def builder() -> str:
        nonlocal builder_calls
        builder_calls += 1
        return "lazy detail"

    try:
        assert runtime_logging.emit_detailed_lazy(builder) is False
        assert builder_calls == 0
        assert stream.getvalue() == ""

        runtime_logging.set_mode(SessionLoggingMode.DETAILED)

        assert runtime_logging.emit_detailed_lazy(builder) is True
        assert builder_calls == 1
        assert stream.getvalue().splitlines() == ["lazy detail"]
    finally:
        runtime_logging.close()
