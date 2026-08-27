r"""Standalone pop-out window for the Steam tab (r439).

Launched as `PuriPulyHeart.exe --steam-window` (or `-m puripuly_heart.main
--steam-window` from source) by the main app's pop-out button. It hosts its own
SteamBridgeView connected to the SAME helper daemon (the daemon is
multi-client), so both windows stay live; the main app shows a "popped out"
screen while this process runs and restores the embedded tab when it exits.
"""

from __future__ import annotations

import asyncio


def run_steam_window() -> int:
    import flet as ft

    from puripuly_heart import boot_stealth as _bs
    _bs.start()

    def _main(page: ft.Page) -> None:
        page.title = "Steam Chat — PuriPulyHeart+"
        page.padding = 0
        page.bgcolor = "#1b1c1f"
        import json as _json

        from puripuly_heart.ui.views.steam_bridge import _PREFS_FILE

        geom = {}
        try:
            geom = (_json.loads(_PREFS_FILE.read_text(encoding="utf-8"))
                    .get("popout_geom") or {})
        except Exception:
            pass
        sizes = (("width", geom.get("w", 1000)),
                 ("height", geom.get("h", 760)),
                 ("left", geom.get("x")),
                 ("top", geom.get("y")),
                 ("min_width", 640), ("min_height", 480))
        for attr, val in sizes:
            if val is None:
                continue
            try:
                setattr(page.window, attr, val)
            except Exception:
                pass

        def _save_geom(_e=None) -> None:
            try:
                if bool(getattr(page.window, "minimized", False)):
                    return   # -32000 phantom coords would strand the window
            except Exception:
                pass
            try:
                d = {}
                try:
                    d = _json.loads(_PREFS_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass
                d["popout_geom"] = {"w": page.window.width,
                                    "h": page.window.height,
                                    "x": page.window.left,
                                    "y": page.window.top}
                _PREFS_FILE.write_text(_json.dumps(d, ensure_ascii=False),
                                       encoding="utf-8")
            except Exception:
                pass

        try:
            page.window.on_event = lambda e: _save_geom()
        except Exception:
            pass

        from puripuly_heart.ui.views.steam_bridge import SteamBridgeView

        view = SteamBridgeView()
        view.expand = True

        async def _on_resized(_e=None):
            # async → runs on the page loop (single-writer); saves geometry
            # and re-fits the tab strip to the new width (debounced — the
            # resize stream fires continuously during a drag)
            _on_resized._gen = getattr(_on_resized, "_gen", 0) + 1
            _g = _on_resized._gen
            await asyncio.sleep(0.2)
            if _g != _on_resized._gen:
                return
            _save_geom()
            try:
                view._rebuild_tabs()
                if view._tab_strip.page:
                    view._tab_strip.update()
            except Exception:
                pass
        page.on_resized = _on_resized
        view.on_popout = None          # no pop-out button inside the pop-out
        view._is_popout = True         # full tab, no module screens in here

        # Translator: honor the tab's picked model, built from the saved app
        # settings (same clone pattern as the main app / OCR bridge).
        _providers: dict = {}

        def _provider_for(model_value: str):
            prov = _providers.get(model_value)
            if prov is not None:
                return prov
            import copy as _copy

            from puripuly_heart.app.wiring import (create_llm_provider,
                                                   create_secret_store)
            from puripuly_heart.config.paths import default_settings_path
            from puripuly_heart.config.settings import (
                TranslationModel, materialize_translation_settings)
            from puripuly_heart.main import _load_settings_or_default

            settings = _load_settings_or_default(default_settings_path())
            if model_value:
                matched = next((m for m in TranslationModel
                                if m.value == model_value), None)
                if matched is not None:
                    settings = _copy.deepcopy(settings)
                    settings.translation.model = matched
            settings = materialize_translation_settings(settings)
            secrets = create_secret_store(settings.secrets,
                                          config_path=default_settings_path())
            prov = create_llm_provider(settings, secrets=secrets,
                                       runtime_logging=None)
            _providers[model_value] = prov
            return prov

        async def _translate(text: str, to_them: bool) -> str:
            import uuid

            mine = getattr(view, "_src_lang", None) or "en"
            theirs = getattr(view, "_tgt_lang", None) or "zh-CN"
            src, tgt = (mine, theirs) if to_them else (theirs, mine)
            llm = None
            try:
                llm = _provider_for(getattr(view, "_tr_provider", "") or "")
            except Exception:
                llm = None
            if llm is None:
                from puripuly_heart.providers.llm.free_web import (
                    FreeWebTranslationProvider)
                llm = FreeWebTranslationProvider("bing")
            res = await llm.translate(
                utterance_id=str(uuid.uuid4()), text=text, system_prompt="",
                source_language=src, target_language=tgt, context="")
            return getattr(res, "text", text) or text

        view.translate_message = _translate
        page.add(view)
        view.activate()
        try:
            page.window.visible = True
            page.update()
            _bs.finish()
        except Exception:
            pass

    ft.app(target=_main, view=ft.AppView.FLET_APP_HIDDEN)
    return 0
