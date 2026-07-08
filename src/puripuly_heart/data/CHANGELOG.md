# Changelog

User-facing changes per build. The latest build's highlights also appear in-app when an update is ready.

## r244 — 2026-07-07
- The status banner (e.g. "Loading speech model") now floats over the top of the chat instead of pushing the chat header and messages down.
- Dropped the "(free)" suffix from translator picker entries — no key required already says it.

## r243 — 2026-07-07
- Model pickers (translator and speech) now list usable options first; greyed-out ones sit at the bottom.

## r242 — 2026-07-07
- Reverted the chat right-click Clear menu: it doubled up with the system's Select-all popup. Clear is back in the chat header.

## r241 — 2026-07-07
- Clear moved from the chat header into a right-click menu on the chat area.
- The header-bar update buttons ("Check for updates", "What's new", and all their states) are now translated in all four UI languages and switch language live.

## r239 — 2026-07-07
- Update downloads no longer pile up on disk: the zip is deleted the moment it's unpacked, and leftovers from past updates are cleaned at every launch (previously only when the About page was opened).

## r238 — 2026-07-07
- Update controls moved fully to the header bar: a labeled "Check for updates" button that shows live progress, next to "What's new".
- The Updates card is gone from the About page.

## r237 — 2026-07-07
- The sidebar update button is now truly gear-sized (the ring drew oversized).
- The update popup title shows the release date.

## r236 — 2026-07-07
- Update check button moved to the top toolbar (far right), with the description as its tooltip.
- New "What's new" button on the About page shows this changelog with dates.
- The Updates card moved to the top of the About page and lost its wall of text.

## r235 — 2026-07-07
- The "Loading speech model" banner no longer shows while all voice channels are off — the warm-up happens silently, and the banner appears only when a channel is actually starting.

## r234 — 2026-07-07
- The sidebar update button now matches the settings gear in size.

## r233 — 2026-07-07
- Updates now show a what's-new popup when they're ready, with a restart button.
- Added this changelog.

## r232 — 2026-07-07
- Updates download automatically in the background; the sidebar button only needs one press to restart and apply.
- New setting: Settings → General → "Automatic update downloads" (turn off to be asked before downloading).

## r231 — 2026-07-07
- Fixed garbled chat entries that leaked the speech model's internal formatting (e.g. "system / language Chinese<asr_text>…").
- The app now warns at startup if a saved preset's language isn't supported by the selected speech model.

## r230 — 2026-07-07
- The speech model picker greys out Qwen ASR (Local) with an explanation when the channel's language isn't supported by it.

## r229 — 2026-07-07
- Picking a language the local speech model can't recognize (e.g. Indonesian, Thai) now shows a clear warning suggesting Whisper or a cloud model.
- Fixed the PEER model picker not reflecting your selection (the switch worked, the label didn't update).

## r228 — 2026-07-06
- Release files renamed to PuriPulyHeartPlus (zip and installer).
- New fork-focused README with a screenshot.

## r227 — 2026-07-06
- New light blue app icon so the fork stands apart from the original (taskbar, window, installer).

## r226 — 2026-07-06
- A notification at launch announces new versions and points to the update button (once per release).
- Failed update downloads now explain why (e.g. GitHub unreachable) and can be retried; the button no longer disappears.

## r225 — 2026-07-06
- Whisper model downloads that are blocked (firewall/offline) now show a clear alert instead of retrying silently.
- The update button applies updates in one click (download → restart automatically).

## r224 — 2026-07-05
- Suppressed garbled multi-line speech-recognition output (number walls, markup fragments) from noisy audio.

## r223 — 2026-07-05
- Saved API keys re-verify automatically at startup, so working providers are no longer greyed out until you visit settings.

## r222 — 2026-07-05
- Saved API keys auto-verify when the settings page loads instead of showing a stale "invalid" icon.

## r221 — 2026-07-05
- The orange Mute Sync tooltip now explains how to enable OSC in VRChat when toggling the mic doesn't help.

## r220 — 2026-07-05
- Switching translators now applies immediately — no more restart needed after picking a new model.
- If a paid translator can't run (missing key), translation falls back to free Google Translate instead of stopping silently.
- Fixed fresh installs translating the other person's speech into Korean regardless of settings.
- A transient key-check failure no longer disables a working translator.

## r219 and earlier — June–July 2026
- One-click in-app updater with a sidebar update button.
- Windows installer (per-user, no admin, downloads the speech model during setup).
- Instant desktop overlay toggling, Output Format menu with pinyin/romaji readings, auto-launch with SteamVR/VRChat, and many stability fixes.
