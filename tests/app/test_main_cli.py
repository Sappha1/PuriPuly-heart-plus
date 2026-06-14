from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import sys
from logging.handlers import QueueHandler
from pathlib import Path
from types import ModuleType

import pytest

import puripuly_heart.main as main_module
from puripuly_heart import __version__
from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterSettings,
    ProviderSettings,
)
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService, configure_main_logging
from puripuly_heart.core.storage.secrets import InMemorySecretStore


def _patch_headless_mic_types(monkeypatch, runner_cls, error_cls=None) -> None:
    if error_cls is None:

        class FakeHeadlessMicInitializationError(Exception):
            pass

        error_cls = FakeHeadlessMicInitializationError

    monkeypatch.setattr(
        main_module,
        "_load_headless_mic_types",
        lambda: (runner_cls, error_cls),
        raising=False,
    )


def test_main_version_prints(capsys) -> None:
    result = main_module.main(["--version"])
    assert result == 0
    assert capsys.readouterr().out.strip() == __version__


def test_main_version_prints_without_soxr_runtime_startup_check(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        main_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: pytest.fail("--version should not run the soxr startup check"),
        raising=False,
    )

    result = main_module.main(["--version"])

    assert result == 0
    assert capsys.readouterr().out.strip() == __version__


def test_main_osc_send_uses_sender(monkeypatch, tmp_path) -> None:
    sent: dict[str, object] = {}

    class FakeSender:
        def __init__(self, *args, **kwargs):
            sent["instance"] = self

        def send_chatbox(self, text: str) -> None:
            sent["text"] = text

        def close(self) -> None:
            sent["closed"] = True

    monkeypatch.setattr(main_module, "VrchatOscUdpSender", FakeSender)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "osc-send", "hello"])

    assert result == 0
    assert sent["text"] == "hello"
    assert sent["closed"] is True


def test_main_osc_send_does_not_require_soxr_runtime_startup_check(monkeypatch, tmp_path) -> None:
    sent: dict[str, object] = {}

    class FakeSender:
        def __init__(self, *args, **kwargs):
            sent["instance"] = self

        def send_chatbox(self, text: str) -> None:
            sent["text"] = text

        def close(self) -> None:
            sent["closed"] = True

    monkeypatch.setattr(
        main_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: pytest.fail("osc-send should not run the soxr startup check"),
        raising=False,
    )
    monkeypatch.setattr(main_module, "VrchatOscUdpSender", FakeSender)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "osc-send", "hello"])

    assert result == 0
    assert sent["text"] == "hello"
    assert sent["closed"] is True


def test_main_run_mic_still_aborts_when_soxr_runtime_startup_check_fails(
    monkeypatch, tmp_path, capsys
) -> None:
    class FakeSoxrRuntimeAvailabilityError(RuntimeError):
        pass

    def raise_runtime_error() -> None:
        raise FakeSoxrRuntimeAvailabilityError("missing packaged soxr sibling dll")

    monkeypatch.setattr(
        main_module,
        "SoxrRuntimeAvailabilityError",
        FakeSoxrRuntimeAvailabilityError,
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "ensure_soxr_runtime_available_for_startup",
        raise_runtime_error,
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "_load_headless_mic_types",
        lambda: pytest.fail("run-mic should abort before loading headless mic types"),
        raising=False,
    )

    config_path = tmp_path / "settings.json"
    vad_model = tmp_path / "vad.onnx"
    vad_model.write_text("dummy", encoding="utf-8")

    result = main_module.main(
        ["--config", str(config_path), "run-mic", "--vad-model", str(vad_model)]
    )

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to verify packaged soxr runtime: missing packaged soxr sibling dll"
    )


def test_main_run_stdin_llm_error(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(main_module, "create_secret_store", lambda *a, **k: "secrets")

    def raise_llm(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "create_llm_provider", raise_llm)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-stdin", "--use-llm"])

    assert result == 2
    assert "failed to initialize LLM provider" in capsys.readouterr().out


def test_main_run_stdin_managed_openrouter_without_release_service_reports_clear_error(
    monkeypatch, tmp_path, capsys
) -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.OPENROUTER),
        openrouter=OpenRouterSettings(selected_source=OpenRouterCredentialSource.MANAGED),
    )
    monkeypatch.setattr(main_module, "_load_settings_or_default", lambda _path: settings)
    monkeypatch.setattr(
        main_module,
        "create_secret_store",
        lambda *a, **k: InMemorySecretStore(),
    )

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-stdin", "--use-llm"])

    output = capsys.readouterr().out
    assert result == 2
    assert "failed to initialize LLM provider" in output
    assert "managed release service" in output


def test_main_run_stdin_invokes_runner(monkeypatch, tmp_path) -> None:
    ran: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            return None

        async def run(self):
            ran["called"] = True
            return 0

    monkeypatch.setattr(main_module, "HeadlessStdinRunner", FakeRunner)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-stdin"])

    assert result == 0
    assert ran["called"] is True


def test_main_run_mic_invokes_runner(monkeypatch, tmp_path) -> None:
    ran: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            return None

        async def run(self):
            ran["called"] = True
            return 0

    _patch_headless_mic_types(monkeypatch, FakeRunner)

    config_path = tmp_path / "settings.json"
    vad_model = tmp_path / "vad.onnx"
    vad_model.write_text("dummy", encoding="utf-8")
    result = main_module.main(
        ["--config", str(config_path), "run-mic", "--vad-model", str(vad_model)]
    )

    assert result == 0
    assert ran["called"] is True


def test_main_run_mic_managed_openrouter_without_release_service_reports_clear_error(
    monkeypatch, tmp_path, capsys
) -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.OPENROUTER),
        openrouter=OpenRouterSettings(selected_source=OpenRouterCredentialSource.MANAGED),
    )

    class FakeHeadlessMicInitializationError(Exception):
        pass

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            assert kwargs["settings"] is settings
            assert kwargs["use_llm"] is True

        async def run(self):
            raise FakeHeadlessMicInitializationError(
                "Headless mic LLM initialization failed: OpenRouter managed mode requires a managed release service; "
                "CLI/headless paths are not wired for managed OpenRouter mode yet"
            )

    monkeypatch.setattr(main_module, "_load_settings_or_default", lambda _path: settings)
    _patch_headless_mic_types(monkeypatch, FakeRunner, FakeHeadlessMicInitializationError)

    config_path = tmp_path / "settings.json"
    vad_model = tmp_path / "vad.onnx"
    vad_model.write_text("dummy", encoding="utf-8")

    result = main_module.main(
        ["--config", str(config_path), "run-mic", "--vad-model", str(vad_model), "--use-llm"]
    )

    output = capsys.readouterr().out
    assert result == 2
    assert "failed to initialize headless mic runner" in output
    assert "managed release service" in output


def test_main_run_mic_runtime_value_error_propagates(monkeypatch, tmp_path) -> None:
    class FakeRunner:
        def __init__(self, *args, **kwargs):
            return None

        async def run(self):
            raise ValueError("runtime boom")

    _patch_headless_mic_types(monkeypatch, FakeRunner)

    config_path = tmp_path / "settings.json"
    vad_model = tmp_path / "vad.onnx"
    vad_model.write_text("dummy", encoding="utf-8")

    with pytest.raises(ValueError, match="runtime boom"):
        main_module.main(["--config", str(config_path), "run-mic", "--vad-model", str(vad_model)])


def test_main_run_gui_invokes_flet_app(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    fake_flet = ModuleType("flet")

    def fake_app(*, target, assets_dir):
        calls["target"] = target
        calls["assets_dir"] = assets_dir

    fake_flet.app = fake_app
    monkeypatch.setitem(sys.modules, "flet", fake_flet)

    fake_ui_app = ModuleType("puripuly_heart.ui.app")

    async def main_gui(page, *, config_path, debug_ui_preview=False):
        _ = page
        calls["config_path"] = config_path
        calls["debug_ui_preview"] = debug_ui_preview

    fake_ui_app.main_gui = main_gui
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.app", fake_ui_app)

    fake_fonts = ModuleType("puripuly_heart.ui.fonts")
    fake_fonts.assets_dir = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.fonts", fake_fonts)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-gui"])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    assert callable(calls["target"])
    asyncio.run(calls["target"](object()))
    assert calls["config_path"] == config_path
    assert calls["debug_ui_preview"] is False


def test_main_default_invokes_gui(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    fake_flet = ModuleType("flet")

    def fake_app(*, target, assets_dir):
        calls["target"] = target
        calls["assets_dir"] = assets_dir

    fake_flet.app = fake_app
    monkeypatch.setitem(sys.modules, "flet", fake_flet)

    fake_ui_app = ModuleType("puripuly_heart.ui.app")

    async def main_gui(page, *, config_path, debug_ui_preview=False):
        _ = page
        calls["config_path"] = config_path
        calls["debug_ui_preview"] = debug_ui_preview

    fake_ui_app.main_gui = main_gui
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.app", fake_ui_app)

    fake_fonts = ModuleType("puripuly_heart.ui.fonts")
    fake_fonts.assets_dir = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.fonts", fake_fonts)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path)])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    assert callable(calls["target"])
    asyncio.run(calls["target"](object()))
    assert calls["config_path"] == config_path
    assert calls["debug_ui_preview"] is False


def test_main_run_gui_passes_debug_ui_preview_flag(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    fake_flet = ModuleType("flet")

    def fake_app(*, target, assets_dir):
        calls["target"] = target
        calls["assets_dir"] = assets_dir

    fake_flet.app = fake_app
    monkeypatch.setitem(sys.modules, "flet", fake_flet)

    fake_ui_app = ModuleType("puripuly_heart.ui.app")

    async def main_gui(page, *, config_path, debug_ui_preview=False):
        _ = page
        calls["config_path"] = config_path
        calls["debug_ui_preview"] = debug_ui_preview

    fake_ui_app.main_gui = main_gui
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.app", fake_ui_app)

    fake_fonts = ModuleType("puripuly_heart.ui.fonts")
    fake_fonts.assets_dir = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.fonts", fake_fonts)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "run-gui", "--debug-ui-preview"])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    asyncio.run(calls["target"](object()))
    assert calls["config_path"] == config_path
    assert calls["debug_ui_preview"] is True


def test_main_run_gui_force_closes_logging_when_gui_runtime_logging_leaks(
    monkeypatch, tmp_path
) -> None:
    root_logger = logging.getLogger(f"test.main.gui.logging.force_close.{tmp_path.name}")
    root_logger.handlers.clear()
    root_logger.propagate = False
    leaked_services: list[SessionRuntimeLoggingService] = []
    monkeypatch.setattr("puripuly_heart.core.runtime_logging.user_config_dir", lambda: tmp_path)

    monkeypatch.setattr(
        main_module,
        "configure_main_logging",
        lambda: configure_main_logging(root_logger=root_logger),
    )

    fake_flet = ModuleType("flet")

    def fake_app(*, target, assets_dir):
        _ = assets_dir
        asyncio.run(target(object()))

    fake_flet.app = fake_app
    monkeypatch.setitem(sys.modules, "flet", fake_flet)

    fake_ui_app = ModuleType("puripuly_heart.ui.app")

    async def main_gui(page, *, config_path, debug_ui_preview=False):
        _ = page, config_path, debug_ui_preview
        leaked_services.append(SessionRuntimeLoggingService(root_logger=root_logger))

    fake_ui_app.main_gui = main_gui
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.app", fake_ui_app)

    fake_fonts = ModuleType("puripuly_heart.ui.fonts")
    fake_fonts.assets_dir = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.fonts", fake_fonts)

    try:
        result = main_module.main(["--config", str(tmp_path / "settings.json"), "run-gui"])

        assert result == 0
        assert leaked_services
        assert [
            handler for handler in root_logger.handlers if isinstance(handler, QueueHandler)
        ] == []
    finally:
        for service in leaked_services:
            service.close()


def test_main_default_gui_passes_debug_ui_preview_flag(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    fake_flet = ModuleType("flet")

    def fake_app(*, target, assets_dir):
        calls["target"] = target
        calls["assets_dir"] = assets_dir

    fake_flet.app = fake_app
    monkeypatch.setitem(sys.modules, "flet", fake_flet)

    fake_ui_app = ModuleType("puripuly_heart.ui.app")

    async def main_gui(page, *, config_path, debug_ui_preview=False):
        _ = page
        calls["config_path"] = config_path
        calls["debug_ui_preview"] = debug_ui_preview

    fake_ui_app.main_gui = main_gui
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.app", fake_ui_app)

    fake_fonts = ModuleType("puripuly_heart.ui.fonts")
    fake_fonts.assets_dir = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "puripuly_heart.ui.fonts", fake_fonts)

    config_path = tmp_path / "settings.json"
    result = main_module.main(["--config", str(config_path), "--debug-ui-preview"])

    assert result == 0
    assert calls["assets_dir"] == str(tmp_path)
    asyncio.run(calls["target"](object()))
    assert calls["config_path"] == config_path
    assert calls["debug_ui_preview"] is True


def test_real_main_gui_accepts_debug_ui_preview_keyword_only() -> None:
    from puripuly_heart.ui.app import main_gui

    parameters = inspect.signature(main_gui).parameters

    assert "debug_ui_preview" in parameters
    debug_ui_preview = parameters["debug_ui_preview"]
    assert debug_ui_preview.kind is inspect.Parameter.KEYWORD_ONLY
    assert debug_ui_preview.default is False


def test_main_local_qwen_runtime_check_dispatches_runner(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def fake_run_local_qwen_runtime_check() -> int:
        calls["called"] = True
        return 0

    monkeypatch.setattr(
        main_module,
        "run_local_qwen_runtime_check",
        fake_run_local_qwen_runtime_check,
        raising=False,
    )

    config_path = tmp_path / "settings.json"
    try:
        result = main_module.main(["--config", str(config_path), "local-qwen-runtime-check"])
    except SystemExit as exc:  # pragma: no cover - red phase guard
        pytest.fail(f"unexpected SystemExit: {exc}")

    assert result == 0
    assert calls["called"] is True


def test_main_soxr_runtime_check_dispatches_runner(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        main_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: None,
        raising=False,
    )

    def fake_run_soxr_runtime_check() -> int:
        calls["called"] = True
        return 0

    monkeypatch.setattr(
        main_module,
        "run_soxr_runtime_check",
        fake_run_soxr_runtime_check,
        raising=False,
    )

    config_path = tmp_path / "settings.json"
    try:
        result = main_module.main(["--config", str(config_path), "soxr-runtime-check"])
    except SystemExit as exc:  # pragma: no cover - red phase guard
        pytest.fail(f"unexpected SystemExit: {exc}")

    assert result == 0
    assert calls["called"] is True


def test_run_soxr_runtime_check_rejects_non_windows(monkeypatch, capsys) -> None:
    try:
        runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")
    except ModuleNotFoundError:  # pragma: no cover - red phase guard
        pytest.fail("soxr_runtime_check module is missing")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "linux", raising=False)
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: pytest.fail("should not validate soxr runtime on non-Windows"),
        raising=False,
    )

    result = runtime_check_module.run_soxr_runtime_check()

    assert result == 2
    assert (
        capsys.readouterr().out.strip() == "Error: soxr-runtime-check is only supported on Windows"
    )


def test_run_soxr_runtime_check_reports_runtime_validation_failure(monkeypatch, capsys) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")

    class FakeSoxrRuntimeAvailabilityError(RuntimeError):
        pass

    def raise_runtime_error() -> None:
        raise FakeSoxrRuntimeAvailabilityError("missing packaged soxr sibling dll")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module,
        "SoxrRuntimeAvailabilityError",
        FakeSoxrRuntimeAvailabilityError,
        raising=False,
    )
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        raise_runtime_error,
        raising=False,
    )

    result = runtime_check_module.run_soxr_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to verify packaged soxr runtime: missing packaged soxr sibling dll"
    )


def test_run_soxr_runtime_check_reports_soxr_import_or_smoke_failure(
    monkeypatch, capsys, tmp_path
) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")

    runtime_paths = type(
        "RuntimePaths",
        (),
        {
            "extension_path": tmp_path / "soxr_ext.cp312-win_amd64.pyd",
            "runtime_dir": tmp_path,
            "sibling_dll_path": tmp_path / "soxr.dll",
        },
    )()

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: runtime_paths,
        raising=False,
    )
    real_import_module = runtime_check_module.importlib.import_module

    def fake_import_module(name: str, *args, **kwargs):
        if name == "soxr":
            raise ImportError("native extension load failed")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(
        runtime_check_module.importlib,
        "import_module",
        fake_import_module,
    )

    result = runtime_check_module.run_soxr_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to import or smoke-test soxr: native extension load failed"
    )


def test_run_soxr_runtime_check_imports_soxr_runs_smoke_and_reports_paths(
    monkeypatch, capsys, tmp_path
) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")
    runtime_dir = tmp_path / "soxr"
    runtime_dir.mkdir()
    extension_path = runtime_dir / "soxr_ext.cp312-win_amd64.pyd"
    extension_path.write_bytes(b"")
    sibling_dll_path = runtime_dir / "soxr.dll"
    sibling_dll_path.write_bytes(b"")

    runtime_paths = type(
        "RuntimePaths",
        (),
        {
            "extension_path": extension_path,
            "runtime_dir": runtime_dir,
            "sibling_dll_path": sibling_dll_path,
        },
    )()
    calls: dict[str, object] = {}

    class FakeResampleStream:
        def __init__(self, in_rate, out_rate, num_channels, dtype="float32"):
            calls["init"] = (in_rate, out_rate, num_channels, dtype)

        def resample_chunk(self, samples, last=False):
            calls["len"] = len(samples)
            calls["last"] = last
            return [0.0, 0.0, 0.0]

    fake_soxr = ModuleType("soxr")
    fake_soxr.ResampleStream = FakeResampleStream

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: runtime_paths,
        raising=False,
    )

    real_import_module = runtime_check_module.importlib.import_module

    def fake_import_module(name: str, *args, **kwargs):
        if name == "soxr":
            return fake_soxr
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(runtime_check_module.importlib, "import_module", fake_import_module)

    result = runtime_check_module.run_soxr_runtime_check()

    assert result == 0
    assert calls["init"] == (48000, 16000, 1, "float32")
    assert calls["len"] == 480
    assert calls["last"] is True
    assert capsys.readouterr().out.strip().splitlines() == [
        f"soxr_extension_path={extension_path}",
        f"soxr_runtime_dir={runtime_dir}",
        f"soxr_sibling_dll={sibling_dll_path}",
    ]


def test_run_soxr_runtime_check_writes_json_report_when_env_var_is_set(
    monkeypatch, tmp_path
) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.soxr_runtime_check")
    report_path = tmp_path / "soxr-runtime-report.json"
    runtime_paths = type(
        "RuntimePaths",
        (),
        {
            "extension_path": Path("C:/temp/soxr/soxr_ext.cp312-win_amd64.pyd"),
            "runtime_dir": Path("C:/temp/soxr"),
            "sibling_dll_path": Path("C:/temp/soxr/soxr.dll"),
        },
    )()

    class FakeResampleStream:
        def __init__(self, in_rate, out_rate, channels, dtype="float32"):
            self.args = (in_rate, out_rate, channels, dtype)

        def resample_chunk(self, samples, last=False):
            return [0.0, 0.0, 0.0]

    fake_soxr_module = type("FakeSoxr", (), {"ResampleStream": FakeResampleStream})
    fake_soxr_ext_module = type("FakeSoxrExt", (), {"__file__": str(runtime_paths.extension_path)})

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)

    def fake_import_module(name: str):
        if name == "soxr":
            return fake_soxr_module
        if name == "soxr.soxr_ext":
            return fake_soxr_ext_module
        raise AssertionError(name)

    monkeypatch.setenv("PURIPULY_HEART_SOXR_RUNTIME_REPORT_PATH", str(report_path))
    monkeypatch.setattr(
        runtime_check_module,
        "ensure_soxr_runtime_available_for_startup",
        lambda: runtime_paths,
    )
    monkeypatch.setattr(
        runtime_check_module,
        "_resolve_loaded_soxr_dll_path",
        lambda: runtime_paths.sibling_dll_path,
    )
    monkeypatch.setattr(runtime_check_module.importlib, "import_module", fake_import_module)

    assert runtime_check_module.run_soxr_runtime_check() == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["expected_extension_path"] == str(runtime_paths.extension_path)
    assert payload["expected_sibling_dll_path"] == str(runtime_paths.sibling_dll_path)
    assert payload["imported_extension_path"] == str(runtime_paths.extension_path)
    assert payload["loaded_sibling_dll_path"] == str(runtime_paths.sibling_dll_path)


def test_run_local_qwen_runtime_check_imports_sherpa_onnx_and_offline_recognizer_before_reporting_success(
    monkeypatch, capsys, tmp_path
) -> None:
    try:
        runtime_check_module = importlib.import_module(
            "puripuly_heart.app.local_qwen_runtime_check"
        )
    except ModuleNotFoundError:  # pragma: no cover - red phase guard
        pytest.fail("local_qwen_runtime_check module is missing")

    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module.local_qwen_runtime,
        "ensure_local_qwen_windows_runtime",
        lambda: tmp_path,
    )

    imported_modules: list[str] = []
    real_import_module = runtime_check_module.importlib.import_module

    def fake_import_module(name: str, *args, **kwargs):
        if name == "sherpa_onnx":
            imported_modules.append(name)
            return ModuleType("sherpa_onnx")
        if name == "sherpa_onnx.offline_recognizer":
            imported_modules.append(name)
            return ModuleType("sherpa_onnx.offline_recognizer")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(runtime_check_module.importlib, "import_module", fake_import_module)

    result = runtime_check_module.run_local_qwen_runtime_check()

    assert result == 0
    assert imported_modules == ["sherpa_onnx", "sherpa_onnx.offline_recognizer"]
    assert capsys.readouterr().out.strip() == f"local_qwen_runtime_dir={tmp_path}"


def test_run_local_qwen_runtime_check_rejects_non_windows(monkeypatch, capsys) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.local_qwen_runtime_check")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "linux", raising=False)

    result = runtime_check_module.run_local_qwen_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: local-qwen-runtime-check is only supported on Windows"
    )


def test_run_local_qwen_runtime_check_reports_bootstrap_failure(monkeypatch, capsys) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.local_qwen_runtime_check")
    runtime_error = importlib.import_module("puripuly_heart.core.local_qwen_runtime")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)

    def raise_bootstrap_error() -> None:
        raise runtime_error.LocalQwenRuntimeBootstrapError("missing runtime dlls")

    monkeypatch.setattr(
        runtime_check_module.local_qwen_runtime,
        "ensure_local_qwen_windows_runtime",
        raise_bootstrap_error,
    )

    result = runtime_check_module.run_local_qwen_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to verify Local Qwen Windows runtime DLL directory: missing runtime dlls"
    )


def test_run_local_qwen_runtime_check_reports_bootstrap_failure_after_runtime_module_reload(
    monkeypatch, capsys, tmp_path
) -> None:
    runtime_check_module = importlib.reload(
        importlib.import_module("puripuly_heart.app.local_qwen_runtime_check")
    )
    runtime_module = importlib.import_module("puripuly_heart.core.local_qwen_runtime")

    runtime_module = importlib.reload(runtime_module)

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(runtime_module.sys, "platform", "win32")

    missing_runtime_dir = tmp_path / "missing-runtime"
    monkeypatch.setattr(
        runtime_module, "resolve_local_qwen_runtime_dir", lambda: missing_runtime_dir
    )

    try:
        result = runtime_check_module.run_local_qwen_runtime_check()
    finally:
        importlib.reload(runtime_check_module)

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to verify Local Qwen Windows runtime DLL directory: "
        f"local qwen runtime directory does not exist: {missing_runtime_dir}"
    )


def test_run_local_qwen_runtime_check_reports_sherpa_onnx_import_failure(
    monkeypatch, capsys, tmp_path
) -> None:
    runtime_check_module = importlib.import_module("puripuly_heart.app.local_qwen_runtime_check")

    monkeypatch.setattr(runtime_check_module, "sys", ModuleType("sys"), raising=False)
    monkeypatch.setattr(runtime_check_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        runtime_check_module.local_qwen_runtime,
        "ensure_local_qwen_windows_runtime",
        lambda: tmp_path,
    )
    real_import_module = runtime_check_module.importlib.import_module

    def fake_import_module(name: str, *args, **kwargs):
        if name == "sherpa_onnx":
            return ModuleType("sherpa_onnx")
        if name == "sherpa_onnx.offline_recognizer":
            raise ImportError("native extension load failed")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(runtime_check_module.importlib, "import_module", fake_import_module)

    result = runtime_check_module.run_local_qwen_runtime_check()

    assert result == 2
    assert capsys.readouterr().out.strip() == (
        "Error: failed to import sherpa_onnx: native extension load failed"
    )


def test_load_settings_or_default_loads_when_exists(monkeypatch, tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")

    sentinel = object()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: sentinel)

    assert main_module._load_settings_or_default(settings_path) is sentinel
