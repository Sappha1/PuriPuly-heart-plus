# -*- coding: utf-8 -*-
"""Real Steam chat + translation injected in-place. Beta, local only.

Instead of rebuilding Steam's chat in the app (an imperfect clone), this opens
the ACTUAL steamcommunity.com/chat in a browser window PuriPuly controls and
weaves translation into it:

  * under each incoming foreign-language message, a translated line is injected;
  * a "type in English" bar is added — you type your language, it is translated
    and sent through Steam's own composer, so it looks and behaves like Steam.

Two Python functions are exposed to the page:
  window.ppTranslate(text)  -> translate their message into your language
  window.ppSend(text)       -> translate your text and send it via Steam

Translation here uses free Bing (standalone). When launched by PuriPuly the app
supplies the user's configured translator + languages over the control socket
(the app owns "my translation settings"); env vars set the language pair.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from steam_page import SteamPage

PROFILE = str(Path(__file__).resolve().parent.parent / "steamprobe-profile")
MY_LANG = os.environ.get("PP_MY_LANG", "en")        # the language you read/type
THEIR_LANG = os.environ.get("PP_THEIR_LANG", "zh-CN")  # what gets sent to them

_TR_MAP = {"en": "en", "zh-CN": "zh", "zh": "zh", "ja": "ja", "ko": "ko",
           "fr": "fr", "de": "de", "es": "es", "ru": "ru", "pt": "pt", "it": "it"}


def _tr(code: str) -> str:
    return _TR_MAP.get(code, code.split("-")[0] if code else "auto")


_cache: dict[tuple, str] = {}


def _bing(text: str, src: str, tgt: str) -> str:
    key = (text, src, tgt)
    if key in _cache:
        return _cache[key]
    try:
        import translators as ts
        out = ts.translate_text(text, translator="bing",
                                from_language=src, to_language=tgt)
        out = str(out).strip()
    except Exception as exc:
        out = f"[translation failed: {type(exc).__name__}]"
    _cache[key] = out
    return out


INJECT_JS = r"""
(ownName) => {
  if (window.__ppInjected) return;
  window.__ppInjected = true;
  window.PP_OWN = ownName || "";

  const style = document.createElement("style");
  style.textContent = `
    .pp-tr { font-size: 12px; color: #8fa9c4; font-style: italic;
             margin-top: 2px; opacity: .95; }
    #pp-bar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 99999;
              display: flex; gap: 8px; padding: 8px 12px;
              background: #1b2838; border-top: 1px solid #2a475e; }
    #pp-bar input { flex: 1; background: #316282; color: #fff; border: none;
                    border-radius: 4px; padding: 8px 10px; font-size: 14px; }
    #pp-bar input::placeholder { color: #9fb4c8; }
    /* keep Steam's own composer clear of our fixed bar */
    body { padding-bottom: 52px !important; }
  `;
  document.head.appendChild(style);

  const bar = document.createElement("div");
  bar.id = "pp-bar";
  const input = document.createElement("input");
  input.placeholder = "Type in your language — Enter sends the translation";
  bar.appendChild(input);
  document.body.appendChild(bar);
  input.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && input.value.trim()) {
      const text = input.value.trim();
      input.value = "";
      input.disabled = true;
      try { await window.ppSend(text); } catch (_) {}
      input.disabled = false;
      input.focus();
    }
  });

  async function annotate(block) {
    if (block.__ppDone) return;
    const speaker = block.querySelector(".speakerName");
    const name = speaker ? speaker.innerText.trim() : "";
    if (name && name === window.PP_OWN) { block.__ppDone = true; return; }
    const msgs = block.querySelectorAll(".msg");
    for (const m of msgs) {
      if (m.__ppDone) continue;
      m.__ppDone = true;
      const text = m.innerText.trim();
      if (!text) continue;
      try {
        const tr = await window.ppTranslate(text);
        if (tr && tr !== text) {
          const div = document.createElement("div");
          div.className = "pp-tr";
          div.textContent = tr;
          m.appendChild(div);
        }
      } catch (_) {}
    }
    block.__ppDone = true;
  }

  function scan() {
    document.querySelectorAll(".ChatMessageBlock").forEach(annotate);
  }
  scan();
  const obs = new MutationObserver(() => scan());
  obs.observe(document.body, {childList: true, subtree: true});
};
"""


async def main() -> int:
    steam = SteamPage(PROFILE)
    await steam.start(mode="login")   # VISIBLE — this IS the Steam window the user sees
    if not await steam.is_signed_in():
        # give the user time to sign in in the window
        await steam.wait_until_signed_in(timeout_s=300)
    own = await steam.own_name()

    page = steam._page

    async def pp_translate(text: str) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, _bing, text, "auto", _tr(MY_LANG))

    async def pp_send(text: str) -> str:
        translated = await asyncio.get_event_loop().run_in_executor(
            None, _bing, text, _tr(MY_LANG), _tr(THEIR_LANG))
        await steam.send(translated)
        return translated

    await page.expose_function("ppTranslate", pp_translate)
    await page.expose_function("ppSend", pp_send)
    await page.evaluate(INJECT_JS, own)

    # Self-check to a log so the injection can be verified without the GUI.
    import io as _io
    _log = _io.open(Path(__file__).resolve().parent / "inject_daemon.log", "w",
                    encoding="utf-8")

    def _note(m: str) -> None:
        _log.write(m + "\n")
        _log.flush()

    try:
        bar = await page.evaluate("() => !!document.getElementById('pp-bar')")
        trs = await page.evaluate("() => document.querySelectorAll('.pp-tr').length")
        bind = await page.evaluate("() => typeof window.ppTranslate === 'function'")
        _note(f"injected bar={bar} translated_lines={trs} binding={bind} own={'set' if own else 'none'}")
        # exercise a translation to prove the binding round-trips
        sample = await pp_translate("你好")
        _note(f"sample translate 你好 -> {sample!r}")
    except Exception as exc:
        _note(f"self-check error: {exc}")

    # Re-inject after SPA navigations (opening a different conversation rebuilds
    # the DOM and drops our bar/observer).
    while True:
        await asyncio.sleep(2)
        try:
            present = await page.evaluate("() => !!document.getElementById('pp-bar')")
            if not present:
                await page.evaluate(INJECT_JS, own)
        except Exception:
            break
    return 0


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
