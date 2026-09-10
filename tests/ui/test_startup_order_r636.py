"""r636 startup order: early recognizer warmup, the Whisper probe off the
critical path, the early window reveal and the deferred-warmup gate.

Page-less: every test drives GuiController directly with a SimpleNamespace app,
patching the heavy collaborators on the CLASS (the controller is slotted, so
instance attributes cannot be created for methods)."""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings import AppSettings, STTProviderName
from puripuly_heart.ui.controller import GuiController


class _FakeRuntimeLogging:
    """Stands in for SessionRuntimeLoggingService (which opens the app log)."""

    def __init__(self) -> None:
        self.mode_calls: list[object] = []
        self.sinks: list[object] = []

    def set_mode(self, mode: object) -> None:
        self.mode_calls.append(mode)

    def attach_realtime_sink(self, sink: object) -> None:
        self.sinks.append(sink)

    def emit_detailed(self, message: str, level: int = 0) -> bool:
        return True

    def emit_basic(self, message: str, level: int = 0) -> bool:
        return True


class _DashboardSpy:
    def __init__(self) -> None:
        self.whisper_availability: list[tuple[bool, str]] = []

    def set_whisper_availability(self, available: bool, note_key: str) -> None:
        self.whisper_availability.append((bool(available), note_key))


def _make_controller(
    *,
    settings: AppSettings | None = None,
    dashboard: object | None = None,
) -> GuiController:
    app = SimpleNamespace()
    if dashboard is not None:
        app.view_dashboard = dashboard
    controller = GuiController(
        page=SimpleNamespace(), app=app, config_path=Path("settings.json")
    )
    controller._runtime_logging = _FakeRuntimeLogging()
    if settings is not None:
        controller.settings = settings
    return controller


def _local_settings(*, peer_enabled: bool = True, eula: bool = True) -> AppSettings:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    settings.ui.peer_translation_enabled = peer_enabled
    settings.ui.peer_translation_eula_accepted = eula
    return settings


# --------------------------------------------------------------------------
# 2. Whisper probe off the critical path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whisper_probe_is_backgrounded_when_no_channel_uses_whisper(monkeypatch):
    dashboard = _DashboardSpy()
    controller = _make_controller(settings=_local_settings(), dashboard=dashboard)
    probed: list[str] = []

    async def _fake_probe(self) -> bool:
        probed.append("probe")
        return True

    monkeypatch.setattr(GuiController, "_probe_whisper_available", _fake_probe)

    await controller._maybe_fallback_whisper_to_local_qwen()

    # Nothing blocked start(): the probe was only scheduled.
    assert probed == []
    assert controller._whisper_probe_task is not None
    await controller._whisper_probe_task
    assert probed == ["probe"]
    # ...and the picker hint still arrives.
    assert dashboard.whisper_availability == [(True, "dashboard.whisper_hub_unreachable")]


@pytest.mark.asyncio
async def test_whisper_probe_runs_inline_when_whisper_is_selected(monkeypatch):
    dashboard = _DashboardSpy()
    settings = _local_settings()
    settings.provider.stt = STTProviderName.WHISPER
    controller = _make_controller(settings=settings, dashboard=dashboard)
    probed: list[str] = []

    async def _fake_probe(self) -> bool:
        probed.append("probe")
        return True

    monkeypatch.setattr(GuiController, "_probe_whisper_available", _fake_probe)

    await controller._maybe_fallback_whisper_to_local_qwen()

    assert probed == ["probe"]
    assert controller._whisper_probe_task is None
    assert dashboard.whisper_availability == [(True, "dashboard.whisper_hub_unreachable")]


@pytest.mark.asyncio
async def test_whisper_peer_channel_also_probes_inline(monkeypatch):
    settings = _local_settings()
    settings.provider.peer_stt = STTProviderName.WHISPER
    controller = _make_controller(settings=settings, dashboard=_DashboardSpy())
    probed: list[str] = []

    async def _fake_probe(self) -> bool:
        probed.append("probe")
        return True

    monkeypatch.setattr(GuiController, "_probe_whisper_available", _fake_probe)

    await controller._maybe_fallback_whisper_to_local_qwen()

    assert probed == ["probe"]
    assert controller._whisper_probe_task is None


# --------------------------------------------------------------------------
# 1. Early STT warmup
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "peer_enabled", "eula", "expected"),
    [
        (STTProviderName.LOCAL_QWEN, True, True, "peer"),
        (STTProviderName.LOCAL_PARAKEET_V3, True, True, "peer"),
        (STTProviderName.LOCAL_QWEN, False, True, None),
        (STTProviderName.LOCAL_QWEN, True, False, None),
        (STTProviderName.DEEPGRAM, True, True, None),
        (STTProviderName.WHISPER, True, True, None),
    ],
)
def test_early_warmup_channel_only_for_saved_local_sherpa_peer(
    provider, peer_enabled, eula, expected
):
    settings = _local_settings(peer_enabled=peer_enabled, eula=eula)
    settings.provider.peer_stt = provider
    controller = _make_controller(settings=settings)

    assert (
        controller._early_local_stt_warmup_channel(peer_enabled=peer_enabled) == expected
    )


@pytest.mark.asyncio
async def test_early_warmup_task_is_scheduled_once(monkeypatch):
    controller = _make_controller(settings=_local_settings())
    runs: list[str] = []

    async def _fake_run(self) -> None:
        runs.append("run")

    monkeypatch.setattr(GuiController, "_run_early_local_stt_warmup", _fake_run)

    controller._schedule_early_local_stt_warmup(peer_enabled=True)
    first = controller._early_stt_warmup_task
    controller._schedule_early_local_stt_warmup(peer_enabled=True)

    assert first is not None
    assert controller._early_stt_warmup_task is first
    await first
    assert runs == ["run"]


@pytest.mark.asyncio
async def test_early_warmup_not_scheduled_for_cloud_peer_provider(monkeypatch):
    settings = _local_settings()
    settings.provider.peer_stt = STTProviderName.DEEPGRAM
    controller = _make_controller(settings=settings)

    async def _fake_run(self) -> None:  # pragma: no cover - must not run
        raise AssertionError("cloud provider must not pre-build a recognizer")

    monkeypatch.setattr(GuiController, "_run_early_local_stt_warmup", _fake_run)

    controller._schedule_early_local_stt_warmup(peer_enabled=True)

    assert controller._early_stt_warmup_task is None


@pytest.mark.asyncio
async def test_early_warmup_warms_backend_prewarms_embedder_and_opens_gate(monkeypatch):
    controller = _make_controller(settings=_local_settings())
    calls: list[str] = []

    class _FakeBackend:
        async def warmup(self) -> None:
            calls.append("warmup")

        async def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(
        "puripuly_heart.ui.controller.create_secret_store",
        lambda *a, **k: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "puripuly_heart.ui.controller.create_peer_stt_backend",
        lambda *a, **k: _FakeBackend(),
    )

    async def _fake_prewarm(self) -> None:
        calls.append("embedder")

    monkeypatch.setattr(GuiController, "_prewarm_speaker_embedder", _fake_prewarm)

    await controller._run_early_local_stt_warmup()

    # The throwaway backend is closed, but only AFTER the shared recognizer
    # has been built and the voiceprint model prewarmed.
    assert calls == ["warmup", "embedder", "close"]
    assert controller.pipeline_settled is True


@pytest.mark.asyncio
async def test_early_warmup_failure_still_opens_the_gate(monkeypatch):
    controller = _make_controller(settings=_local_settings())

    def _boom(*a, **k):
        raise RuntimeError("model missing")

    monkeypatch.setattr(
        "puripuly_heart.ui.controller.create_secret_store", lambda *a, **k: SimpleNamespace()
    )
    monkeypatch.setattr("puripuly_heart.ui.controller.create_peer_stt_backend", _boom)

    await controller._run_early_local_stt_warmup()

    assert controller.pipeline_settled is True


# --------------------------------------------------------------------------
# 4. Deferred-warmup gate
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_opens_when_the_pipeline_reports_settled():
    controller = _make_controller(settings=_local_settings())
    assert controller.pipeline_settled is False

    waiter = asyncio.ensure_future(controller.await_pipeline_settled(timeout=5.0))
    await asyncio.sleep(0)
    assert not waiter.done()

    controller.note_pipeline_settled()
    await asyncio.wait_for(waiter, timeout=1.0)
    assert controller.pipeline_settled is True


@pytest.mark.asyncio
async def test_gate_opens_on_timeout_and_releases_every_waiter():
    controller = _make_controller(settings=_local_settings())

    await controller.await_pipeline_settled(timeout=0.01)

    assert controller.pipeline_settled is True
    # A later waiter returns immediately once the gate is open.
    await asyncio.wait_for(controller.await_pipeline_settled(timeout=5.0), timeout=1.0)


@pytest.mark.asyncio
async def test_transliteration_warmup_is_skipped_when_no_reading_line_is_shown():
    settings = _local_settings()
    for flag in ("chat_show_pinyin", "chat_show_romaji", "chat_show_romaja",
                 "chat_show_latin", "show_pinyin", "show_romaji", "show_latin",
                 "send_pinyin", "send_romaji", "send_latin"):
        setattr(settings.ui, flag, False)
    settings.overlay.show_romanization = False
    controller = _make_controller(settings=settings)

    assert controller._any_reading_line_enabled() is False
    controller._schedule_deferred_transliteration_warmup()
    assert controller._deferred_translit_task is None


@pytest.mark.asyncio
async def test_transliteration_warmup_waits_for_the_gate():
    settings = _local_settings()
    settings.ui.chat_show_pinyin = True
    controller = _make_controller(settings=settings)

    controller._schedule_deferred_transliteration_warmup()
    task = controller._deferred_translit_task
    assert task is not None
    await asyncio.sleep(0)
    assert not task.done()          # still parked on the gate

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# --------------------------------------------------------------------------
# 3. Early window reveal
# --------------------------------------------------------------------------


class _StartAborted(Exception):
    """Ends start() at _init_pipeline so no hub/bridge is needed."""


@pytest.mark.asyncio
async def test_reveal_callback_fires_before_the_probe_and_the_pipeline(monkeypatch):
    order: list[str] = []
    settings = _local_settings(peer_enabled=False)
    controller = _make_controller(settings=settings)

    monkeypatch.setattr(
        GuiController, "_load_or_init_settings", lambda self, path: settings
    )
    monkeypatch.setattr(GuiController, "_sync_ui_from_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_sync_overlay_calibration_cache", lambda self, s: None
    )
    monkeypatch.setattr(
        GuiController,
        "_schedule_deferred_transliteration_warmup",
        lambda self: order.append("translit_scheduled"),
    )

    async def _fake_whisper(self) -> None:
        order.append("whisper_probe")

    async def _fake_init_pipeline(self) -> None:
        order.append("init_pipeline")
        raise _StartAborted

    monkeypatch.setattr(
        GuiController, "_maybe_fallback_whisper_to_local_qwen", _fake_whisper
    )
    monkeypatch.setattr(GuiController, "_init_pipeline", _fake_init_pipeline)

    with pytest.raises(_StartAborted):
        await controller.start(lambda: order.append("reveal"))

    assert order.index("reveal") < order.index("whisper_probe")
    assert order.index("reveal") < order.index("init_pipeline")
    assert order[0] == "reveal"


@pytest.mark.asyncio
async def test_start_without_a_reveal_callback_still_runs(monkeypatch):
    order: list[str] = []
    settings = _local_settings(peer_enabled=False)
    controller = _make_controller(settings=settings)

    monkeypatch.setattr(
        GuiController, "_load_or_init_settings", lambda self, path: settings
    )
    monkeypatch.setattr(GuiController, "_sync_ui_from_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_sync_overlay_calibration_cache", lambda self, s: None
    )
    monkeypatch.setattr(
        GuiController, "_schedule_deferred_transliteration_warmup", lambda self: None
    )

    async def _fake_whisper(self) -> None:
        order.append("whisper_probe")

    async def _fake_init_pipeline(self) -> None:
        order.append("init_pipeline")
        raise _StartAborted

    monkeypatch.setattr(
        GuiController, "_maybe_fallback_whisper_to_local_qwen", _fake_whisper
    )
    monkeypatch.setattr(GuiController, "_init_pipeline", _fake_init_pipeline)

    with pytest.raises(_StartAborted):
        await controller.start()

    assert order == ["whisper_probe", "init_pipeline"]
