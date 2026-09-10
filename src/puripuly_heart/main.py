from __future__ import annotations

import os

# r636: cap the BLAS/OpenMP worker pools BEFORE numpy can load (nothing above
# this line imports it; the imports below are stdlib + config.paths +
# runtime_logging, none of which touch numpy). Uncapped, OpenBLAS spawned 23
# threads and committed ~818 MB in EACH of the app's three processes for math
# it never does at that scale. ORT and sherpa size their own pools and are
# deliberately left alone. Child processes inherit these via the environment.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from puripuly_heart.config.paths import default_settings_path, default_vad_model_path
from puripuly_heart.core.runtime_logging import OVERLAY_LOG_FILENAME, configure_main_logging

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


HeadlessStdinRunner: Any | None = None
VrchatOscUdpSender: Any | None = None
SoxrRuntimeAvailabilityError: type[Exception] | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="puripuly-heart")
    parser.add_argument("--version", action="store_true", help="Print version and exit")

    parser.add_argument(
        "--config",
        type=Path,
        default=default_settings_path(),
        help="Path to settings JSON (default: user config dir)",
    )
    parser.add_argument(
        "--debug-ui-preview",
        action="store_true",
        default=False,
        help="Show developer-only GUI preview controls for hidden UI states",
    )

    sub = parser.add_subparsers(dest="command")

    osc_send = sub.add_parser("osc-send", help="Send a single VRChat chatbox OSC message")
    osc_send.add_argument("text", help="Text to send")

    stdin = sub.add_parser("run-stdin", help="Read lines from stdin and send to OSC")
    stdin.add_argument(
        "--use-llm",
        action="store_true",
        help="Translate each line using configured LLM provider (requires provider setup)",
    )

    mic = sub.add_parser("run-mic", help="Capture microphone audio (VAD→STT→LLM→OSC)")
    mic.add_argument(
        "--vad-model",
        type=Path,
        default=default_vad_model_path(),
        help="Path to Silero VAD ONNX model file (default: user config dir)",
    )
    mic.add_argument(
        "--use-llm",
        action="store_true",
        help="Translate STT final results using configured LLM provider",
    )

    desktop_overlay = sub.add_parser(
        "run-desktop-overlay",
        help="Run the desktop Flet overlay renderer",
    )
    desktop_overlay.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to overlay launch manifest JSON",
    )
    sub.add_parser(
        "run-desktop-overlay-preview",
        help="Run the desktop Flet overlay preview",
    )

    sub.add_parser(
        "local-qwen-runtime-check",
        help="Verify the Local Qwen Windows runtime DLL directory",
    )
    sub.add_parser(
        "soxr-runtime-check",
        help="Verify the packaged soxr runtime contract and smoke resample",
    )

    run_gui = sub.add_parser("run-gui", help="Run the Graphical User Interface (Flet)")
    run_gui.add_argument(
        "--debug-ui-preview",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Show developer-only GUI preview controls for hidden UI states",
    )

    return parser


def _print_initialization_error(component: str, exc: Exception) -> int:
    print(f"Error: failed to initialize {component}: {exc}", flush=True)
    return 2


def _print_runtime_error(component: str, exc: Exception) -> int:
    print(f"Error: failed to verify {component}: {exc}", flush=True)
    return 2


def _load_headless_mic_types():
    from puripuly_heart.app.headless_mic import HeadlessMicInitializationError, HeadlessMicRunner

    return HeadlessMicRunner, HeadlessMicInitializationError


def _load_headless_stdin_runner():
    global HeadlessStdinRunner
    if HeadlessStdinRunner is None:
        from puripuly_heart.app.headless_stdin import HeadlessStdinRunner as LoadedRunner

        HeadlessStdinRunner = LoadedRunner
    return HeadlessStdinRunner


def _load_vrchat_osc_udp_sender():
    global VrchatOscUdpSender
    if VrchatOscUdpSender is None:
        from puripuly_heart.core.osc.udp_sender import VrchatOscUdpSender as LoadedSender

        VrchatOscUdpSender = LoadedSender
    return VrchatOscUdpSender


def _soxr_runtime_availability_error_type() -> type[Exception]:
    global SoxrRuntimeAvailabilityError
    if SoxrRuntimeAvailabilityError is None:
        from puripuly_heart.core.soxr_runtime import (
            SoxrRuntimeAvailabilityError as LoadedError,
        )

        SoxrRuntimeAvailabilityError = LoadedError
    return SoxrRuntimeAvailabilityError


def ensure_soxr_runtime_available_for_startup():
    from puripuly_heart.core.soxr_runtime import ensure_soxr_runtime_available_for_startup as run

    return run()


def run_local_qwen_runtime_check() -> int:
    from puripuly_heart.app.local_qwen_runtime_check import run_local_qwen_runtime_check as run

    return run()


def run_soxr_runtime_check() -> int:
    from puripuly_heart.app.soxr_runtime_check import run_soxr_runtime_check as run

    return run()


def create_secret_store(*args, **kwargs):
    from puripuly_heart.app.wiring import create_secret_store as create

    return create(*args, **kwargs)


def create_llm_provider(*args, **kwargs):
    from puripuly_heart.app.wiring import create_llm_provider as create

    return create(*args, **kwargs)


def load_settings(path: Path):
    from puripuly_heart.config.settings import load_settings as load

    return load(path)


def new_settings_for_first_run():
    from puripuly_heart.config.settings import new_settings_for_first_run as make_settings

    return make_settings()


def _requires_soxr_runtime_startup_check(args: argparse.Namespace) -> bool:
    return args.command == "run-mic"


def _run_gui(config_path: Path, *, debug_ui_preview: bool) -> int:
    import flet as ft

    from puripuly_heart.ui.app import main_gui
    from puripuly_heart.ui.fonts import assets_dir

    async def _target(page: ft.Page):
        return await main_gui(
            page,
            config_path=config_path,
            debug_ui_preview=debug_ui_preview,
        )

    # Hidden start: the flet boot window must never flash (the bundled
    # client IGNORES FLET_APP_HIDDEN — verified live). The stealth state
    # lives in boot_stealth (its own module) because this entry runs as
    # __main__ when frozen: state stored HERE is invisible to
    # `import puripuly_heart.main` (a second module instance) — which is
    # exactly how r455 armed a watchdog nobody could disarm.
    from puripuly_heart import boot_stealth
    boot_stealth.start()
    ft.app(target=_target, assets_dir=str(assets_dir()),
           view=ft.AppView.FLET_APP_HIDDEN)
    _stop_steam_helper()
    return 0


def _exit_without_finalization(rc: int, logging_sinks) -> int:
    """Frozen builds: end the process NOW, skipping interpreter finalization.

    r626: closing the app while a local-STT utterance was mid-decode froze
    the process until force-kill. The decode runs on an abandoned daemon
    thread inside native onnxruntime; interpreter finalization then tears
    down the sherpa/ORT objects that thread is still executing in, and the
    native destructor blocks holding the GIL — freezing every thread, the
    logging included. Nothing in this app relies on finalization (no atexit
    hooks; settings persist on change; log records flush per-emit), so once
    the GUI/mic loop has returned and the sinks are closed, os._exit is the
    only exit that cannot be taken hostage by a wedged native call.

    Source runs (tests, dev) keep normal returns — PURIPULY_NO_HARD_EXIT=1
    forces that even when frozen, for exit-path diagnosis.
    """
    if not getattr(sys, "frozen", False) or os.environ.get("PURIPULY_NO_HARD_EXIT"):
        return rc
    try:
        logging_sinks.close(force=True)
    except Exception:
        pass
    os._exit(rc)


def _stop_steam_helper() -> None:
    """The Steam helper daemon + its hidden browser are separate processes:
    nothing stopped them when the app itself exited, so they kept the Steam
    web session alive after close (r624 — it was still pushing 'friend is
    playing' toasts). quit makes the daemon close its browser and exit."""
    import socket

    try:
        s = socket.create_connection(("127.0.0.1", 8791), timeout=0.5)
        try:
            s.sendall(b'{"cmd": "quit"}\n')
        finally:
            s.close()
    except Exception:
        pass


def _acquire_single_instance_lock() -> bool:
    """True if this is the only GUI instance. r647: the mutex now lives in
    puripuly_heart.single_instance (its own module, so the frozen __main__
    entry and the shutdown path share one handle) and is RELEASED at
    begin_shutdown - so this needs no wait: a genuine duplicate is rejected
    instantly (no boot-splash lingering), and a close-then-relaunch acquires
    immediately because the closing instance already released the mutex."""
    from puripuly_heart import single_instance

    return single_instance.acquire()


def _run_launch_wrapper(game_argv: list[str]) -> int:
    """VRChat launch-options wrapper mode: `PuriPulyHeart.exe --launch %command%`.

    Starts the translator as a detached process (single-instance guarded, so an
    already-running translator is reused), then runs the wrapped game command in the
    foreground and mirrors its exit code — keeping Steam's "game running" tracking
    accurate while the translator outlives the game.
    """
    import subprocess

    if getattr(sys, "frozen", False):
        translator_cmd = [sys.executable]
    else:
        translator_cmd = [sys.executable, "-m", "puripuly_heart.main"]

    detached_flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB — escape Steam's job object
    try:
        subprocess.Popen(translator_cmd, creationflags=detached_flags | breakaway, close_fds=True)
    except OSError:
        with_suppress = None
        try:
            subprocess.Popen(translator_cmd, creationflags=detached_flags, close_fds=True)
        except OSError as exc:  # translator failed to start — still launch the game
            with_suppress = exc
        if with_suppress is not None:
            print(f"Warning: failed to start translator: {with_suppress}", flush=True)

    completed = subprocess.run(game_argv)
    return int(completed.returncode or 0)


def _run_desktop_overlay(config_path: Path) -> int:
    from puripuly_heart.ui.desktop_overlay import main as desktop_overlay_main

    return desktop_overlay_main(["--config", str(config_path)])


def _run_desktop_overlay_preview() -> int:
    from puripuly_heart.ui.desktop_overlay import main as desktop_overlay_main

    return desktop_overlay_main(["--preview"])


def main(argv: list[str] | None = None) -> int:
    # `--launch <command...>` (Steam launch options wrapper: PuriPulyHeart.exe --launch
    # %command%) must be handled before argparse — the wrapped game command is an
    # arbitrary argv tail that argparse would reject.
    raw_argv = list(sys.argv[1:]) if argv is None else list(argv)
    if "--launch" in raw_argv:
        launch_index = raw_argv.index("--launch")
        wrapped_command = raw_argv[launch_index + 1 :]
        if wrapped_command:
            return _run_launch_wrapper(wrapped_command)
        raw_argv = raw_argv[:launch_index]
    if "--steam-window" in raw_argv:
        from puripuly_heart.ui.steam_window import run_steam_window

        return run_steam_window()
    argv = raw_argv

    # r636: the overlay renderer subprocess gets its own log file. Sharing
    # puripuly_heart.log with the GUI process made every rollover fail on
    # Windows (rename of a file the other process holds) and silently dropped
    # the rolling side's records. Decided on raw argv because the file has to
    # be open before argparse runs (parse errors are logged too).
    if "run-desktop-overlay" in raw_argv or "run-desktop-overlay-preview" in raw_argv:
        logging_sinks = configure_main_logging(log_filename=OVERLAY_LOG_FILENAME)
    else:
        logging_sinks = configure_main_logging()
    # r354: before anything can import huggingface_hub, which reads its
    # endpoint once at import time. A user behind the Great Firewall had every
    # model download time out, which left them on the one recogniser their CPU
    # cannot run accurately.
    try:
        from puripuly_heart.core.model_mirror import configure_model_downloads

        configure_model_downloads(
            os.environ.get("PURIPULY_MODEL_SOURCE", "auto").strip().lower() or "auto"
        )
    except Exception:
        logging.getLogger(__name__).debug("mirror setup skipped", exc_info=True)
    try:
        parser = build_parser()
        args = parser.parse_args(argv)

        if args.version:
            from puripuly_heart import __version__

            print(__version__)
            return 0

        try:
            if _requires_soxr_runtime_startup_check(args):
                ensure_soxr_runtime_available_for_startup()
        except _soxr_runtime_availability_error_type() as exc:
            return _print_runtime_error("packaged soxr runtime", exc)

        # GUI paths only: refuse to start a second app instance (e.g. SteamVR
        # auto-launch firing while the app is already open). The desktop-overlay
        # renderer subcommand is a legitimate second process of this exe and is
        # exempt.
        if args.command in (None, "run-gui") and not _acquire_single_instance_lock():
            # r647: a genuine duplicate (the app really is running, mutex held)
            # is rejected instantly - close the boot splash the moment we know,
            # so it does not linger. A close-then-relaunch does not reach here:
            # the closing instance releases the mutex at begin_shutdown.
            try:
                from puripuly_heart import boot_splash
                boot_splash.close()
            except Exception:
                pass
            print("PuriPulyHeart is already running.", flush=True)
            return 0

        if args.command in (None, "run-gui"):
            # r649: honour the "show boot splash" setting. The splash is drawn by
            # the exe bootloader BEFORE Python runs, so it cannot be suppressed
            # outright - but if the user turned it off we close it now (it barely
            # registers) instead of animating it. Read the flag straight from the
            # settings file so this stays ahead of the slow model load.
            _splash_on = True
            try:
                import json as _json

                _sp = args.config
                if _sp is not None and Path(_sp).is_file():
                    _sd = _json.loads(Path(_sp).read_text(encoding="utf-8"))
                    _splash_on = bool(_sd.get("ui", {}).get("show_boot_splash", True))
            except Exception:
                _splash_on = True
            # r643: animate the percentage as early as possible so it climbs
            # during the slow numpy / onnxruntime import below (frozen only).
            try:
                from puripuly_heart import boot_splash
                if _splash_on:
                    boot_splash.start_progress()
                else:
                    boot_splash.close()
            except Exception:
                pass

        if args.command in (None, "run-gui"):
            # r636: GUI process only. This used to run for EVERY subcommand,
            # and its int8 probe imports numpy + onnxruntime -- the overlay
            # renderer paid the whole OpenBLAS pool (~818 MB, 23 threads) to
            # write a [SysInfo] line the GUI process had already written.
            from puripuly_heart.core.system_info import log_system_info_async

            log_system_info_async()

        if args.command == "run-desktop-overlay":
            return _run_desktop_overlay(args.config)

        if args.command == "run-desktop-overlay-preview":
            return _run_desktop_overlay_preview()

        if args.command == "run-gui":
            return _exit_without_finalization(
                _run_gui(
                    args.config,
                    debug_ui_preview=bool(getattr(args, "debug_ui_preview", False)),
                ),
                logging_sinks,
            )

        if args.command == "local-qwen-runtime-check":
            return run_local_qwen_runtime_check()

        if args.command == "soxr-runtime-check":
            return run_soxr_runtime_check()

        settings = _load_settings_or_default(args.config)

        if args.command == "osc-send":
            sender_cls = _load_vrchat_osc_udp_sender()
            sender = sender_cls(
                host=settings.osc.host,
                port=settings.osc.port,
                chatbox_address=settings.osc.chatbox_address,
                chatbox_send=settings.osc.chatbox_send,
                chatbox_clear=settings.osc.chatbox_clear,
            )
            try:
                sender.send_chatbox(args.text)
            finally:
                sender.close()
            return 0

        if args.command == "run-stdin":
            llm = None
            if args.use_llm:
                try:
                    secrets = create_secret_store(settings.secrets, config_path=args.config)
                    llm = create_llm_provider(settings, secrets=secrets)
                except Exception as exc:
                    return _print_initialization_error("LLM provider", exc)

            runner_cls = _load_headless_stdin_runner()
            runner = runner_cls(settings=settings, llm=llm)
            return asyncio.run(runner.run())

        if args.command == "run-mic":
            HeadlessMicRunner, HeadlessMicInitializationError = _load_headless_mic_types()
            runner = HeadlessMicRunner(
                settings=settings,
                config_path=args.config,
                vad_model_path=args.vad_model,
                use_llm=args.use_llm,
            )
            try:
                return _exit_without_finalization(asyncio.run(runner.run()), logging_sinks)
            except HeadlessMicInitializationError as exc:
                return _print_initialization_error("headless mic runner", exc)

        # Default: run GUI when no command specified (e.g., double-clicking EXE)
        if args.command is None:
            return _exit_without_finalization(
                _run_gui(
                    args.config,
                    debug_ui_preview=bool(getattr(args, "debug_ui_preview", False)),
                ),
                logging_sinks,
            )

        parser.print_help()
        return 2
    finally:
        logging_sinks.close(force=True)


def _load_settings_or_default(path: Path) -> AppSettings:
    if path.exists():
        return load_settings(path)
    return new_settings_for_first_run()


if __name__ == "__main__":
    raise SystemExit(main())
