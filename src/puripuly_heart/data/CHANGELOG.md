# Changelog

User-facing changes per build. The latest build's highlights also appear in-app when an update is ready.

## r298 — 2026-07-27

- New "Mic noise suppression" option (Settings, next to mic sensitivity): cleans steady background noise like fans and AC from your microphone before speech recognition — for setups where speech gets eaten as noise. Off by default
- After 3 unrecognizable mic transcriptions the chat now shows an actionable notice (check the input device / enable noise suppression) instead of staying silent until the 20th

## r297 — 2026-07-27

- Fixed English messages tagged [ZH] under auto-detect: the language tag sniffed the text but fell back to the pinned language for Latin script — English now tags [EN] (and gets English treatment for readings), Chinese still tags [ZH]

## r296 — 2026-07-26

- "Ignore my language" is now a checkbox (matching the reading toggles) and only appears while Auto Detect Voice is on

## r295 — 2026-07-26

- New option under Auto Detect Voice: "Ignore my language" — while auto-detect is on, speech detected as YOUR language (e.g. your own voice echoing through the call) is dropped. Off by default; options menu, indented under the auto-detect row
- Note from the log check: switching to a favorites tab pins peer voice to that tab's language — other languages get filtered by design (the chat shows a notice naming the dropped language)

## r294 — 2026-07-26

- The auto-detect badge is aligned exactly over the +/- button column (it sat slightly too far right)

## r293 — 2026-07-26

- The swap arrow no longer scrambles languages while auto-detect is on (it silently exchanged the hidden pinned values and moved extra rows between sections) — swap is disabled until auto-detect is off
- The auto-detect badge now sits on the "Target language" label row itself, right-aligned — bound to the field it controls
- While auto-detect is on, extra pinned language rows and the + are hidden entirely (they're inactive); they come back when you turn it off

## r292 — 2026-07-26

- The auto-detect badge moved to the Translation card's header, next to the swap and options icons at the top right — the language rows are back to their normal layout

## r291 — 2026-07-26

- Fixed the auto-detect badge disappearing after adding an extra language with + — only the + hides at the cap now, so auto-detect can always be turned off
- While auto-detect is on, extra pinned language rows dim to show they're inactive (auto-detect accepts every language)

## r290 — 2026-07-26

- The auto-detect badge is now anchored to the top-right of the language row (above the +), matching the other controls' alignment

## r289 — 2026-07-26

- Received chat entries now show the detected language while voice auto-detect is on ("Received [ZH] 16:37") — uses the translator's detection when available, script analysis otherwise

## r288 — 2026-07-26

- The auto-detect badge now sits above the + button instead of squeezing the language card (no more truncated "Chinese (Simplifi...")
- Turning auto-detect on now visually renames the picker to "Auto Detect" (and back to the pinned language when off)
- The filtered-voice notice now uses your UI language for language names (no more "English" dropped into a Chinese sentence)
- The overlay right-click menu no longer shows "show my text/messages" as ON after a restart when you had turned them off — the saved choice always applied, but the menu lied

## r287 — 2026-07-26

- HOTFIX: r286's auto-detect badge and TRANS-off dimming were attached to the hidden separate-layout card — they now appear on the unified Translation card everyone actually sees (badge sits next to the Target language picker)

## r286 — 2026-07-26

- When incoming voice is dropped by the PEER language filter, the chat now tells you what happened and names the language ("Heard English speech, but PEER voice is set to Chinese…"), repeating at most every 5 minutes — no more silent nothing
- New auto-detect badge next to the Target language field: teal = incoming voice recognized in any language, grey = only the chosen PEER language passes; click it to toggle
- The language panel dims while TRANS is off (with an explanatory tooltip), so it no longer looks like translation is active when it isn't

## r285 — 2026-07-26

- The per-language chat reading checkboxes now only appear when the Chat log format actually includes a reading line (e.g. Original + Pinyin + Translation) — with Original + Translation they were shown ticked while doing nothing
- Removed stray trailing dots from idle labels ("Remove OCR module", "Ready to translate", "Custom")

## r284 — 2026-07-26

- The per-language reading toggles now also cover the chat feed: options menu → under the "Chat log" format, tick/untick Chinese pinyin, Japanese romaji, Korean romaja, other Latin — independent from the overlay's toggles
- Chat readings follow the same native default: your own UI language's reading starts hidden (zh → pinyin, ja → romaji, ko → romaja)
- Menu labels no longer end in a garbled ellipsis on some fonts ("Remove OCR module..." and friends now use plain dots)

## r283 — 2026-07-26

- New per-language reading lines on the overlay: right-click the overlay button → Display → untick just the readings you don't need (Chinese pinyin, Japanese romaji, Korean romaja, other Latin) — a Chinese reader can hide pinyin while keeping Korean romaja
- The reading for your own UI language now starts hidden by default (a zh user doesn't need pinyin; ja → romaji hidden; ko → romaja hidden) — flip it back on anytime in the same menu
- "Requires API key" in the Settings pickers is now translated (it always showed in English)

## r282 — 2026-07-26

- Fixed the desktop overlay failing with "bridge authentication failed" for users running a proxy/VPN tool (very common in China): the overlay's local connection was being routed into the system proxy — it now always connects directly
- If the overlay process ever dies (e.g. killed by antivirus), turning the overlay on again now works instead of being locked out until an app restart
- Overlay "can't connect" and "authentication rejected" are now reported as separate errors so the real cause is visible

## r281 — 2026-07-24

- Peer audio now survives headphone unplugs and device changes: if the capture feed dies (unplug, Bluetooth drop, driver power event) the app detects it within ~10 seconds and reconnects to whatever output device is live — no more silent deafness until restart
- When your chosen output device comes back after being unplugged, capture switches back to it automatically
- All of this is logged clearly ("Reopening desktop capture…", "Capture reconnected…") so device trouble is visible instead of invisible

## r280 — 2026-07-21

- Fixed every message printing TWICE in the chat log when translation is off — two code paths were both writing the same line; now there is exactly one
- With voice auto-detect, untranslated speech on the overlay is now labeled by its actual script — English no longer gets routed through Chinese reading treatment

## r279 — 2026-07-21

- The auto-updater's internal package is renamed to "updater-payload-internal.zip" so nobody mistakes it for the app download

## r278 — 2026-07-21

- New "Remove OCR module…" option in the OCR right-click menu — frees ~340 MB; turning OCR on downloads it again anytime

## r277 — 2026-07-20

- Realtime OCR is now an optional module: the installer has a checkbox (ticked by default), and slim installs get a one-time in-app download (~150 MB) the first time OCR is used
- Updates are now roughly half the size — the updater downloads a slim package and leaves your installed OCR module untouched

## r276 — 2026-07-20

- The VRChat send checkbox moved up next to Clear with shorter wording ("To VRChat"); the composer has more breathing room

## r275 — 2026-07-20

- The API tab System prompt box is collapsed by default — click the "System prompt" header to expand it (the prompt is still used when collapsed)

## r274 — 2026-07-20

- API Requests feed redesigned: color-coded entry cards (teal = outgoing, blue = response), muted metadata, the prompt in an indented block — and the feed no longer collides with the System prompt field

## r273 — 2026-07-20

- API tab: new "Also send to VRChat chatbox" checkbox (off by default) pushes the composer result in-game like a dashboard message
- The "NOT sent" note now lists which models support prompts (Gemma, DeepSeek, Gemini, Qwen, Local LLMs)

## r272 — 2026-07-20

- Clear button on the API Requests page, like the chat box

## r271 — 2026-07-20

- The API tab text box clears after sending, like the chat box

## r270 — 2026-07-20

- Fixed the API tab composer: pressing Enter/Send crashed silently (a leftover reference from the tab move) — sends work now
- The System prompt box prefills with the app's active prompt when you open the tab, ready to edit ("(editable)" caption removed)

## r269 — 2026-07-20

- API Requests is now its own tab (new icon in the top bar) instead of a mode inside the Logs page

## r268 — 2026-07-20

- API Requests view fixes: Enter sends (Shift+Enter for newline, same send button as the chat box), manual sends use your dashboard Target language (no more missing target_lang error), log lines no longer bleed into the view, the header button no longer gets cut off, and typed messages are labeled with the right channel instead of "peer_final"

## r267 — 2026-07-18

- New API Requests view in the Logs page: see every request sent to translation servers, wire-accurate per provider (DeepL/free-web entries say plainly that the prompt is NOT sent — only your text and languages are)
- The view includes a composer: edit the system prompt, type a text, and hand-send it to the active provider to see the raw response

## r266 — 2026-07-18

- Fixed transcripts sometimes appearing twice: a startup race could open two speech sessions on the same recognizer, doubling every line until a restart
- The installer now always shows the folder chooser, so you can install to another drive even when upgrading

## r265 — 2026-07-17

- HOTFIX: fresh installs crashed at first launch ("translation connection_history connection is not supported") — a leftover default from the removed managed-key system; stale entries now self-heal instead of crashing

## r264 — 2026-07-17

- "Log API request content" now sits above the API keys box


- The Windows firewall prompt is gone by default: push-to-talk mute sync is now an opt-in toggle (Settings → General → "Push-to-talk Mute Sync"). Toggle-mute users sync on their first mic flip exactly as before, with no network discovery at all

## r262 — 2026-07-17

- The dashboard TRANS label now shows when a fallback is active (e.g. "Gemini 3 Flash → Bing") instead of pretending the selected model is serving
- The Prompt page's request format now shows the full API message (model + system + user), matching what LLM servers actually receive
- "Log API request content" moved to Settings → API, next to the keys
- The one-time Windows firewall prompt is now explained in the log and README (it's the VRChat mute-sync discovery; local network only)

## r261 — 2026-07-17

- New setting: "Log API request content" (Settings → General, off by default) — writes the exact text, context, and instructions sent to translation servers into the program log
- Prompt page: new read-only "Request content format" card showing the exact template wrapped around your text when sent to LLM servers

## r260 — 2026-07-17

- Mute Sync now reads VRChat's current mic state directly — no more toggling your in-game mic once to sync, and it finally works with push-to-talk

## r259 — 2026-07-17

- Removed the upstream "managed free key" system entirely — no more prompts pointing to the original project's Discord; DeepSeek Flash and Gemma now use your own OpenRouter/DeepSeek key
- The dashboard TRANS label now updates when you change the model in the gear Settings (it used to only track the dashboard's own picker)
- Fixed the overlay randomly hiding and re-showing itself (with the "overlay active" banner) — a double-fired click event bounced it; toggles are debounced now

## r257 — 2026-07-17

- Dashboard right-click pickers again grey out models without a working API key — enter keys via the gear Settings, whose picker stays unrestricted
- When a selected model has no working key, the app now says so in red (instead of silently translating with a free engine and pretending)
- Entering a valid API key now takes effect immediately — previously the free-engine fallback stayed active until an app restart
- The no-key fallback engine is now Bing (works in China) instead of Google (blocked there)

## r255 — 2026-07-17

- Turning translation off no longer hides incoming voice — the chat log now shows the untranslated lines (same for your own mic)
- Bing translation rebuilt on Microsoft's Edge service: no API key, works in mainland China, no more timeouts or crashes
- The Settings translation picker (gear icon) now also lets you select key-needing models so their key fields appear
- Changing the translator from the dashboard now updates the Settings page display instantly (it used to show a stale model)
- The app re-checks for updates every 2 hours, so the update button appears mid-session instead of only at launch

## r254 — 2026-07-16

- Translator models that need an API key are selectable again — pick the model, then its key field appears in Settings → API (translation uses a free engine until the key is entered)
- DeepL's key field now only appears while DeepL is the selected translator, like every other model
- China: Bing translation connects to the China servers automatically — fixes the 10-second timeouts
- Free web translation retries once before giving up on a slow connection

## r253 — 2026-07-16

- Fixed the false "local speech model isn't working" popup: garbage from the other person's call audio no longer blames your speech model
- Audio buffers are 8x deeper — slower PCs no longer drop (and garble) audio while the speech model loads
- Logs now start with a system snapshot (CPU, RAM, GPU, Windows build, Smart App Control) so bug reports diagnose themselves
- When Windows blocks a program file (antivirus / Smart App Control), the log now names the file and the likely cause with the fix
- Fewer antivirus false alarms: all exes and the installer now carry proper publisher/version information

## r252 — 2026-07-15

- China: speech model now downloads from ModelScope first on Chinese systems (HuggingFace is blocked there)
- Fewer antivirus false alarms: builds are no longer UPX-compressed
- Installer: optional "clean install" checkbox wipes old settings and app data for a fresh start
- Overlay keeps itself on top — no more invisible subtitles under the game
- Speech recognition: runaway repeated-character output is collapsed instead of flooding the screen
- Fixed a legacy hidden setting that forced incoming voice into Korean

## r251 — 2026-07-15

- Peer voice model now loads once at startup — the first speech no longer pays a surprise 7-9s load
- Auto-detected voice gets the right reading line: Japanese speech shows romaji (not pinyin), Korean shows romaja
- Speech-model hallucinations (endoftext garbage) are truncated instead of printed
- Fixed a translation-provider regression from the r250 hotfix

## r250 — 2026-07-15

- Fixed OCR detection not working in installed builds (the bundled recognition engine failed to load)

## r249 — 2026-07-14

- Right-click menus now scroll instead of getting cut off when the window is small

## r248 — 2026-07-14

- Realtime OCR translation: read chat bubbles and world text straight off the screen (ALT+E)
- Redesigned Translation card: Your language / Target language with one-click swap
- Independent Chat log format: send translation-only in game while the log shows everything
- Typed chat messages now auto-detect what you typed and translate into the partner's language
- Bug fixes: typed-message translation failures, alt-tab/taskbar issues, peer model reloads

## r247 — 2026-07-07
- The settings gear now stays dead-center; the update button fades in to its right without moving anything.

## r246 — 2026-07-07
- The settings gear no longer shifts when the update button appears — the button's slot is always reserved.

## r245 — 2026-07-07
- The update popup no longer disappears from a stray click outside it — it stays until you pick "Later" or "Restart now".
- The status banner now sits attached across the top of the chat box instead of floating loose over it.

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
