# Changelog

User-facing changes per build. The latest build's highlights also appear in-app when an update is ready.

## r234
- The sidebar update button now matches the settings gear in size.

## r233
- Updates now show a what's-new popup when they're ready, with a restart button.
- Added this changelog.

## r232
- Updates download automatically in the background; the sidebar button only needs one press to restart and apply.
- New setting: Settings → General → "Automatic update downloads" (turn off to be asked before downloading).

## r231
- Fixed garbled chat entries that leaked the speech model's internal formatting (e.g. "system / language Chinese<asr_text>…").
- The app now warns at startup if a saved preset's language isn't supported by the selected speech model.

## r230
- The speech model picker greys out Qwen ASR (Local) with an explanation when the channel's language isn't supported by it.

## r229
- Picking a language the local speech model can't recognize (e.g. Indonesian, Thai) now shows a clear warning suggesting Whisper or a cloud model.
- Fixed the PEER model picker not reflecting your selection (the switch worked, the label didn't update).

## r228
- Release files renamed to PuriPulyHeartPlus (zip and installer).
- New fork-focused README with a screenshot.

## r227
- New light blue app icon so the fork stands apart from the original (taskbar, window, installer).

## r226
- A notification at launch announces new versions and points to the update button (once per release).
- Failed update downloads now explain why (e.g. GitHub unreachable) and can be retried; the button no longer disappears.

## r225
- Whisper model downloads that are blocked (firewall/offline) now show a clear alert instead of retrying silently.
- The update button applies updates in one click (download → restart automatically).

## r224
- Suppressed garbled multi-line speech-recognition output (number walls, markup fragments) from noisy audio.

## r223
- Saved API keys re-verify automatically at startup, so working providers are no longer greyed out until you visit settings.

## r222
- Saved API keys auto-verify when the settings page loads instead of showing a stale "invalid" icon.

## r221
- The orange Mute Sync tooltip now explains how to enable OSC in VRChat when toggling the mic doesn't help.

## r220
- Switching translators now applies immediately — no more restart needed after picking a new model.
- If a paid translator can't run (missing key), translation falls back to free Google Translate instead of stopping silently.
- Fixed fresh installs translating the other person's speech into Korean regardless of settings.
- A transient key-check failure no longer disables a working translator.

## r219 and earlier
- One-click in-app updater with a sidebar update button.
- Windows installer (per-user, no admin, downloads the speech model during setup).
- Instant desktop overlay toggling, Output Format menu with pinyin/romaji readings, auto-launch with SteamVR/VRChat, and many stability fixes.
