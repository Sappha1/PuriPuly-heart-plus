# beta_local — never ships

Experimental, local-only. NOT part of any build, never pushed, never merged to
dev/main. Lives only on the `beta/steam-bridge` branch.

## steam_bridge — Steam ⇄ translator bridge (proof of concept)

A standalone tool that runs a hidden Steam web-chat in Edge (Playwright), reads a
friend's messages, translates them, and sends your translated replies back — the
full loop, to validate the design before it becomes real dashboard tabs.

- `tracker.py`   — pure new-message + echo-suppression logic (unit tested)
- `steam_page.py`— async wrapper around the hidden Edge (list/open/read/send)
- `harness.py`   — the Flet window tying it together (keyless Bing translate)

Runs from an ISOLATED venv (playwright + flet + httpx) in the session scratchpad,
deliberately separate from the app's .venv so it can never bloat a release build.

Status: reading, sending, conversation list, own-name + echo suppression all
proven on a real account. Next phase (not done): fold into the app as tabs, over
a local websocket like the overlay/OCR processes.
