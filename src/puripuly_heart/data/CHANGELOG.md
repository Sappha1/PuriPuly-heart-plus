# Changelog

User-facing changes per build. The latest build's highlights also appear in-app when an update is ready.

## r469 — 2026-08-12

- Steam Chat: the image viewer now matches Steam — the frame fits the picture exactly (no more empty bands), with a clear ✕ in the corner; clicking anywhere outside also closes it
- Steam Chat: images from Steam's newer CDN render inline instead of appearing as bare links
- Steam Chat: sending a link or filename no longer produces a near-identical "translation" duplicate — links in translated messages ride along untranslated
- Steam Chat: the module power screen is tidier — Turn off and Sign out sit side by side, and Sign out moved there from the settings menu

## r462 — 2026-08-11

- Steam Chat: status changes work — set Online / Away / Invisible / Offline from the menu on your name, with instant feedback; going back online after Offline reconnects automatically
- Steam Chat: the status menu now matches Steam, including Do Not Disturb (mutes the unread dots) and profile shortcuts
- Steam Chat: chats reopened after closing their tab also load instantly from the local cache
- Fixed: the unread dot no longer lights up on chats you just messaged yourself
- Fixed: "Show pinyin / romaji" now applies to your own messages — the reading is taken from the translation
- Fixed: the "No messages here yet" note disappears when the first message arrives
- Fixed: your sent message's translation line could vanish if the chat refreshed at the wrong moment — it now always stays
- Fixed: flicker when opening a chat — cached translations render together with the first paint

## r459 — 2026-08-11

- Steam Chat settings: the scrollbar no longer covers the toggles, and a new "Translate my messages" option shows your own messages untranslated when off
- "Sign out of Steam" now lives on the module power screen (the cog menu is shorter), and the settings gear works from that screen too

## r458 — 2026-08-11

- Steam Chat: chats are cached to disk and preloaded in the background — tabs and chat switches render instantly, scroll position included
- Steam Chat: pop-out window, full emoticon/sticker picker (tabs, search, recents, hover cards), sticker and room-effect sending, per-tab scroll position, character counter, and a tab menu (close / close to the right / close all)
- Steam Chat: pick a translator model just for this tab; translations are cached per model so switching never re-spends API credits
- Settings: new Modules section to add or remove the Realtime OCR and Steam Chat modules; the installer now offers fetching the Steam module too
- Fixed: extra language slots added on one preset tab no longer appear on the other preset tabs
- Fixed: no window flash at startup — the app appears only once it is fully loaded

## r438 — 2026-08-11

- Steam Chat: switching chats now swaps instantly — chats you've opened before appear immediately from cache instead of re-loading, the previous chat never lingers under the new tab, and an empty chat says so instead of showing a blank pane

## r437 — 2026-08-11

- Steam Chat module fixes: sending images works now, and a message typed while the connection is briefly down is held with a notice and sent automatically once it reconnects, instead of being lost

## r436 — 2026-08-11

- **New optional module: Steam Chat.** Chat with your Steam friends in their language from a new Steam tab — incoming messages are translated for you and yours are translated for them. Install or remove it any time under Settings → General → Modules. It's brand new, so there may be some bugs

## r391 — 2026-08-09

- **The local speech model now works when your Windows account name is not written in English letters.** If your account name (or the folder you unzipped into) contains Chinese, Japanese, Korean, Cyrillic or accented characters, the speech engine could not read its own model files: it failed deep inside the audio library and closed the whole app, with nothing written to the log and no error on screen. From the outside the app simply froze on startup or vanished. Nothing could be done about it either, because the model is stored under your account folder. The app now hands the engine an equivalent path Windows keeps for exactly this purpose, and the model loads normally
- This was previously mistaken for a memory problem. It was not: it happened with plenty of memory free, on every attempt, and the r386 memory improvements — one shared model instead of two — remain worthwhile on their own

## r390 — 2026-08-09

- **The workspace address box now only appears when it applies.** r389 added it to the API keys list for everyone, including the majority whose keys never need it — one more thing to interpret for no reason. It now shows up only while a workspace-style key is being entered, or when an address is already saved so it stays editable. With an ordinary key you see the same two fields as before

## r389 — 2026-08-08

- **Alibaba workspace API keys (sk-ws-…) now work.** The Singapore Model Studio console has begun issuing workspace keys, which only authenticate against a private per-workspace address shown above the key in the console — on the shared address this app used, every request is rejected as an invalid key, which looks exactly like a broken purchase. Settings now has a field for that address under the Singapore key: paste it, and the key works. Normal sk- keys need nothing and behave as before
- **Entering a workspace key without its address now says so.** Instead of a bare "verification failed", the check recognises the sk-ws- prefix and tells you, in your interface language, to paste the workspace address from the console

## r388 — 2026-08-08

- **The first caption after a quiet spell no longer flashes squashed.** The compressed "smushed" first caption is an old bug with an old fix — a corrective resize that runs when the first caption arrives. But that fix armed only at startup, on overlay off/on, and un-minimize, and it fires once. The caption window empties a few seconds after every turn, and the first caption after each lull rendered with nothing to correct it — briefly squashed until the next line arrived and laid out normally. That edge now gets the same corrective as the overlay reveal: a one-pixel size pulse, imperceptible, applied only while the overlay is locked

## r387 — 2026-08-07

- **Stray "The system." lines from silence are gone — without blocking anyone from saying it.** The local speech model produces this phrase from noise, but a person can also genuinely say it, so it could not simply be banned. The measurements decided it: every noise emission came from a minimum-length audio segment (under two seconds), while real speech containing the word ran much longer. The phrase is now filtered only when it is the entire line AND the audio behind it is that short. Said inside any sentence it always goes through; said deliberately on its own at talking pace it goes through too; and when the duration isn't known, nothing is filtered

## r386 — 2026-08-07

- **The local speech model now loads once and is shared, instead of twice at the same time.** Your voice and your partner's voice each built their own copy of the exact same model — about 1.1 GB apiece — and built them simultaneously at startup. On a machine without several GB of memory free, that double demand paged the whole system into a freeze: the app locked to a spinner, the load never finished, and nothing was ever written to the log. One copy now serves both, loads are queued so two can never build at once, and the model survives settings changes instead of reloading
- **If there genuinely isn't enough free memory, the app now says so instead of freezing.** Below the required headroom the load refuses with a message showing how much is needed and how much is free, and suggests closing other programs or switching to a cloud recognizer — in your interface language. Previously the only symptom was a machine so busy paging that not even an error could be written

## r385 — 2026-08-07

- **The overlay's stay-on-top guard now finds its own window.** It has never found it. The guard exists so a full-screen game cannot bury the captions, and it re-checks every few seconds — but it was looking for a window belonging to the app's own process, and the window does not belong to that process. The app runs the interface server and starts a second program to draw with; that second program owns the window. So the search matched nothing, on every launch, in every build that has shipped it, and the overlay could still be pushed underneath a game with nothing in the log but a single warning. It now searches the programs the app actually started, prefers the real caption window when several match, and still says so if it comes up empty
- **A message you type in the language you are translating INTO now appears on the overlay.** The overlay deliberately holds a finished line back until its translation arrives, so the caption doesn't flash the untranslated text first. Translation is skipped when what you typed is already in the target language — but the hold was not released on that path, so the line stayed held forever: it went to the VRChat chatbox and simply never appeared as a caption. Toggling the overlay did not bring it back, because nothing was left to redraw

## r384 — 2026-08-07

- **"Show my own text" and "show my own voice" on the overlay now do what they say.** With voice off and text on — an ordinary combination — nothing you typed ever reached the overlay, and no amount of toggling could fix it. Two separate checks guard your own messages: one knows whether a message was typed or spoken, the other only knows that it is yours, and the second was discarding the whole channel whenever the voice setting was off. That coarse check now only asks whether you want any of your own messages at all, and leaves the choice between typed and spoken to the one that can tell them apart. The dashboard switch also saves the setting to both places it is kept — it wrote only one, so turning your own voice back on there was quietly undone at the next restart — and now takes effect on the running overlay immediately instead of at the next full settings save
- **"A system." no longer appears out of nowhere while you are speaking.** The local speech model produces it from short or quiet audio, and it reached the subtitles and the chatbox — once tacked onto the end of a real sentence. It was already on the list of the model's known stock phrases, but that list was compared against the exact wording, and the model writes "A system." rather than "system", so the entry had never once fired. Stock phrases are now matched with capitalisation and punctuation ignored, and only when they are the entire line — a real sentence that happens to contain the word is left alone

## r383 — 2026-08-07

- **Naming a voice sticks again when two saved voices sound alike.** If two saved people scored within a hair of each other, the app refused to choose between them — correctly — but that refusal also stopped it applying the name the speaker's own group already carried, so every line came back unnamed no matter how clearly it belonged to that person. Naming it again added another sample, which made the two voices even more alike, so it could never recover. A group that already belongs to someone now keeps that name; deciding which of two strangers a new voice is still needs a clear margin, and "this is not that person" still overrides everything

## r382 — 2026-08-07

- **Reverted r378: the language you pick for your partner is no longer sent to the speech recogniser.** It was meant to help when the recogniser guessed wrong, but it did far more harm than good. In a room where everyone was speaking English with the partner language left on Chinese, the recogniser *translated* every line into Chinese instead of writing it down — and the translator then turned that Chinese back into English, so the speaker's own sentence came back to them through a round trip, reading perfectly naturally with nothing to show it had happened. The setting only helped when it matched what was actually being spoken, and nothing could tell a correct setting from a stale one

## r381 — 2026-08-06

- **The overlay no longer disappears behind full-screen games.** It stays on top by re-asserting itself every few seconds, but that self-heal could never find its own window, so it had been doing nothing at all. The window was still marked always-on-top the whole time — it had simply been pushed underneath the game, which is why it looked switched on while nothing appeared on screen. If it ever fails to find its window now, it says so in the log instead of failing silently

## r380 — 2026-08-06

- **Clicking an unnamed speaker no longer suggests someone else's name.** A line showing "Speaker 3" opened the naming box already filled in with a saved name — someone that line was not. A session's speaker group remembers whoever was recognised in it, and the app deliberately withholds that name from a voice that does not sound enough like them; the naming box was then asking for it anyway. Saving would have merged the two voices into one identity, which is exactly what the merge warning exists to prevent — except silently, because the box looked like it was confirming a name rather than creating one
- **Custom vocabulary now starts empty.** New installs were seeded with two example names that meant nothing to anyone else, and the feature switched itself on to use them. It now starts off, with nothing in it, until you add your own

## r379 — 2026-08-06

- **A long setting name no longer hides its own button.** "Separate 'Text Translation' box" ran past the edge of its row and pushed the On/Off control out of sight, so the setting could be read but not changed. Every settings row now lets a long name wrap to two lines and trim with "…" instead of overflowing, and the value column keeps its place whatever the label says — this affects all languages, where names are often longer than the English

## r378 — 2026-08-05

- **Choosing a language for your partner's voice now actually affects recognition.** With "Auto detect voice" turned OFF, the language you pick is passed to the local speech model; previously it was discarded, so the setting did nothing at all for that model. This is the way out if your partner's speech is being written down in the wrong language — pin the language they actually speak
- Auto detect is unchanged and still decodes without a language hint. That is deliberate: forcing the *wrong* language on this model makes it translate rather than transcribe, which would make foreign speech look like the language you expected

## r377 — 2026-08-05

- **Fixed settings and the find bar showing English on a non-English interface.** "Show what's new after updates", "Mic auto-gain", "Mic noise suppression", "Their volume auto-gain", "Speaker identification", "Manage", the On/Off values beside them, and the find box's "Find in chat" all stayed English however the interface language was set. None of it was a missing translation — every one of those was already translated in all four languages. The screens are built before your saved language is applied, so every label starts in English and only the ones on an internal list get corrected afterwards; these were never added to it

## r376 — 2026-08-05

- **The find results now keep up with the conversation.** With the find bar open, a message arriving underneath it was never searched: it stayed unhighlighted and the counter stopped moving. Every new, updated or cleared message now re-runs the search. Your place is kept — the highlight does not jump to the newest match every time someone speaks — and the chat is not scrolled out from under you while you read

## r375 — 2026-08-05

- **Enter now keeps stepping through matches** instead of working once. Shift+Enter steps backwards
- **Pressing Ctrl+F while a search is already in the box lets you type straight over it**, the way a browser does. The old query stays put if you press Enter instead, so you can repeat the same search. (The text is not shown highlighted — the interface toolkit this app is built on cannot draw a selection inside an input — but typing replaces it exactly as if it were)

## r374 — 2026-08-04

- **The find panel now sits compactly in the top-right corner** instead of stretching across the chat. r373 relied on the overlay layer to both size and position it and got neither — it spanned nearly the full width and sat on the left
- **The typed text is centred in the panel** rather than riding along its top edge
- **The match counter reads 0/0 when nothing is found.** It used to say "No results", which did not fit and rendered as "No resul"

## r373 — 2026-08-04

- **The find bar is now a small floating panel in the top-right corner of the chat**, the way a browser does it, instead of a full-width strip across the top. It hovers over the messages rather than pushing them down, so opening and closing it no longer reflows the chat, and it is a little chunkier with a proper drop shadow

## r372 — 2026-08-04

- **Ctrl+F now actually opens the find bar.** The shortcut first checks that the dashboard is the view on screen, and it was checking the wrong container — the one holding the top bar *and* the view, rather than the view itself — so the check could never pass and the key did nothing at all
- **Tab to swap your languages works again**, which had been dead the same way for as long as the top bar has existed

## r371 — 2026-08-04

- **Ctrl+F searches the chat log**, the way it does everywhere else. A find bar opens at the top of the chat box: type to highlight every match, Enter or the arrows to step through them, Esc to close. The counter shows which match you are on and how many there are, and it starts on the newest one — a chat is read from the bottom, and the first match is usually far above what you are looking at

## r370 — 2026-08-02

- **The Outline edge style now reads as an outline.** Its colour was always black — the problem was the width. The rim was a fixed one pixel against captions that draw at 41–56px, roughly 2% of a character's height, where a video player's outline is nearer 6%. At that width it looks like slightly bolder text rather than a black edge. The rim now scales with the size actually drawn, and a second rim at half the width fills the gaps that open on the diagonals
- **The Caption Style menu now opens on your saved settings.** The edge style and text background controls were never filled in from what was stored, so the slider always read 0% and the style always read the default however you had left them. A change is only sent to the overlay when it differs from what is saved, so any adjustment that happened to land on the stored value was dropped in silence — which is why the text background sometimes needed moving away and back before it took

## r369 — 2026-08-02

- **Edge style and Text background now work.** The caption settings were being reset to their defaults at the last step before drawing: the overlay re-validates its display settings by copying them through another object, and the two new ones were left out of that copy — so whatever you picked was replaced with the default a moment before the caption was built

## r368 — 2026-08-02

- **Text background is now set on the caption control itself, not only inside its text style.** The values were confirmed reaching the overlay and the control, so if the style block is being overridden this is the setting that will show it

## r367 — 2026-08-02

- **Text background now applies to lines that show a reading above the characters** (pinyin or romaji over the original). Those are drawn by different code from plain lines, and only the plain one had been updated — so on most captions the background could never appear
- The overlay now records the caption styling it receives in the normal log, instead of only when detailed logging is switched on

## r366 — 2026-08-02

- **Edge style and Text background now actually change the overlay.** Two more places listed their fields by hand and dropped them: the code that sends a settings change to the overlay while it is running, and the code that writes settings to disk — so the options did nothing, and would also have been forgotten on restart

## r365 — 2026-08-02

- **Fixed the new caption options doing nothing.** Edge style and Text background were saved correctly and never reached the overlay — it draws in a separate process, and the settings that cross to it are listed one by one, so anything not on that list is dropped. Both now apply as soon as you pick them

## r364 — 2026-08-02

- **Fixed the new overlay options being cut off** at the menu's width — the edge style choices ran off the right edge. Edge style is now a compact picker that expands when clicked, the same control Size and Display use, so it fits however long the option names are in your language

## r363 — 2026-08-02

- **Caption appearance now lives in the overlay's right-click menu** — character edge style (None, Drop shadow, Raised, Depressed, Outline) and a new **Text background** slider
- **Text background** is a box drawn behind the letters themselves, separate from the existing panel behind the whole caption area. Raise it to keep text readable over a bright or busy scene without dimming everything. Off by default, so nothing about your overlay changes until you touch it
- Both settings apply to the VR overlay as well as the desktop one

## r362 — 2026-08-01

- **Character edge style for the overlay captions** — None, Drop shadow, Raised, Depressed or Outline, the same choices a video player gives you. Captions sit over whatever is on screen, so a drop shadow can vanish against a bright scene where an outline stays readable
- (Background opacity behind the captions was already adjustable — Settings → Overlay, or the overlay's own right-click menu)

## r361 — 2026-08-01

- **Short replies are matched against a bar suited to their length.** A brief "yeah" carries less information than a full sentence, so it scores lower against its own speaker — judging both by one number made short replies fail to recognise the person who just spoke
- The log now records how close the nearest existing speaker was whenever a NEW one is opened, so a voice splitting into several speakers can actually be diagnosed instead of guessed at

## r360 — 2026-08-01

- **Two people who sound nothing alike are no longer given the same name.** The app was building a voiceprint even from near-silent audio — one reported case was recorded at a level where 42% of the samples were pure silence — and a voiceprint made of silence describes nobody, so it landed inside another person's identity. Speech that is too quiet or too broken up now produces no voiceprint at all
- Every received message now shows a speaker tag, either a name or "Unknown speaker". Lines that were too faint to identify say so when you hover them, instead of silently looking like a different kind of message
- The log now records which speaker a voice was matched to and how close the match was, so a wrong assignment can actually be explained afterwards

## r359 — 2026-08-01

- **Fixed received messages failing whenever translation was switched on.** r356 added the speaker voiceprint to translated messages but missed that this message type builds itself by hand, so it rejected the new information and the message errored out. Only visible with translation enabled, which is why it slipped through

## r358 — 2026-08-01

- **Uncertain voices are told apart again instead of all showing "Unknown speaker".** A short clip now gets its own Speaker number like anyone else. It still cannot alter a saved voice — that was the part actually worth protecting, and it stays protected
- **The first speaker in a chat is now Speaker 1.** The number shown was an internal counter that also counted audio you never saw — filtered messages, discarded noise — so a fresh session could open at "Speaker 2". It now counts speakers as they appear in the chat, and clearing the chat starts the count over

## r357 — 2026-08-01

- **Renamed the tag on a line whose speaker isn't saved yet, from "Unidentified" to "Unknown speaker".** The app heard a person, transcribed them and translated them — "Unidentified" made it sound as though it had failed to hear anything, or that what it heard might not be a person. It knows someone spoke; it just doesn't know who yet. Hovering the tag now explains what clicking it will do

## r356 — 2026-08-01

- **Fixed the Unidentified tag not actually appearing.** r352 added it, but every received line reaches the chat by a different route than the one that was updated, so the voiceprint never arrived and the tag stayed a plain "Received" header with nothing to click
- **If your processor cannot run the compact speech model accurately, the app now says so on the dashboard**, in your own language, instead of only writing it to a log file

## r355 — 2026-08-01

- The check added in r354 now **measures** whether your processor's compressed-model arithmetic is correct, by running a tiny calculation whose answer is known in advance, instead of inferring it from the processor's name

## r354 — 2026-08-01

- **Speech models can now be downloaded on networks that cannot reach the usual host.** On some networks (mainland China in particular) every model download timed out, which left only the built-in recognizer available. Downloads can now be routed through a mirror of the same files; they are still checksum-verified, and nothing changes for everyone else

## r353 — 2026-08-01

- **The app now records whether your processor can run the compressed speech model accurately.** Some older processors lack an instruction that the compressed model's arithmetic depends on. Where it is missing, speech recognition can return fluent, confident nonsense while appearing to work perfectly — the app now says so in the log instead of leaving you to guess

## r352 — 2026-08-01

- **You can now name a speaker the app could not identify.** Those lines used to show a plain "Received" header with nothing to click, so the one person who knew who was talking had no way to say so. They now carry an **Unidentified** tag — click it to name them, and that voice is learned from the message itself

## r351 — 2026-08-01

- **Major accuracy work on voice identification.** Two people could be merged into a single identity at a similarity the app itself considered too weak to share a name; that is fixed, and a name is now only applied when it clearly beats the next closest person rather than merely clearing a fixed bar. When two people are genuinely too close to call, the line is left unnamed instead of guessed
- **The limit of 12 speakers per session is now 64.** Past the old limit the app handed the next speaker somebody else's identity, and naming that line saved the wrong person's voice permanently
- A borderline match no longer edits a saved voice, so one mistake can no longer make the next mistake more likely

## r350 — 2026-08-01

- **"This is not that person" is now remembered.** Correcting a wrong name only lasted until that person spoke again — the correction was not recorded anywhere, so the app re-applied the same wrong name. Corrections now survive restarts, and naming someone still overrides an earlier correction

## r349 — 2026-08-01

- **More accurate voice identification, using a larger and better model.** Previously saved voices are cleared once when you update, because the new model describes a voice differently — the Saved voices panel explains this. You will need to name people again, once
- **Very short clips no longer invent new speakers.** Under about two seconds there is not enough audio to tell people apart reliably, which is what produced long lists of "Speaker N" for a room holding two people. Short clips can still be recognised as someone already named; they just cannot create somebody new

## r348 — 2026-08-01

- Fixed a stray character or two wrapping onto a line of their own beneath a full-width subtitle line

## r347 — 2026-07-31

- **Long subtitles no longer run off the overlay.** A long message now shrinks its text just enough to fit the overlay size you chose, instead of having the translation cut off. Short messages are unchanged, and the same fix applies to the VR overlay and to small overlay sizes on laptops
- Subtitles can also use up to 10 wrapped lines (was 6), which by itself was cutting the end off longer translations

## r346 — 2026-07-31

- **"Only translate my target languages" now works even when the speech recognizer rewrites what it hears.** When a recognizer is set to one language and someone speaks another, it can silently translate their speech into the expected language instead of transcribing it — so the filter saw the right characters and let the message through. The app now checks what language the audio actually was
- Because it checks the audio rather than the characters, the filter also works for languages that don't use a distinct script (previously it could not tell, for example, French speech from English)

## r345 — 2026-07-31

- The naming window now has a **Manage saved voices…** link, and a new **Clear this speaker's name** option that sends the line back to "Speaker N" while keeping the saved voice
- **Deleting a saved voice clears its name from the chat log immediately**

## r344 — 2026-07-31

- **Two people no longer fragment into "Speaker 8".** Three fixes to voice matching: near-miss samples join the clearly-nearest speaker, stray fragments merge back automatically (never across two named people), and naming someone no longer freezes their voice profile — the root cause of repeated re-naming
- Giving an anonymous "Speaker N" the name of someone already saved is now a friendly **Add voice** action instead of a merge warning — and it's undoable

## r343 — 2026-07-31

- **60 interface strings that ignored the language setting are now translated** — the entire About page, dashboard menus and tooltips, the OCR color menu, and several settings tooltips
- Fixed: the sidebar collapse tooltip lost its translation after one click, "Pinyin" appeared in English inside Chinese labels, and seven Chinese/Japanese/Korean entries were still English

## r342 — 2026-07-31

- Naming window options no longer get cut off — each choice has a short label with a wrapping description
- New **Only relabel this message** option: changes just that line and saves nothing (handy for screenshots)

## r341 — 2026-07-31

- The naming window now tells you how many messages a rename will update, and lets you choose: rename this person everywhere, or "only this speaker — a different person", which separates that voice without touching anyone else
- Typing a name that already exists turns Save into an explicit **Merge** button, with a warning showing both voiceprint counts (up to 4 are kept). The Saved voices list asks you to save a second time instead
- New **Undo last change** in Saved voices — renaming, merging and deleting keep one step of undo

## r340 — 2026-07-30

- Internal cleanup: name labels belonging to cleared or trimmed chat entries no longer linger in memory for the rest of the session

## r339 — 2026-07-30

- Every chat line showing a name can now be clicked to rename — lines recognised purely by voiceprint previously had no click target, and renaming skipped them

## r338 — 2026-07-30

- **Renaming a voice now renames that person everywhere.** If someone was named from several different messages, every one of their entries in the chat log updates at once instead of just the one you clicked
- **A different speaker can no longer be given someone else's name.** A voice that wasn't close enough to be recognised could still inherit a name just by sounding vaguely like a voice from the same conversation — that's fixed, and unsure lines now show "Speaker N" instead of guessing a name
- **New: Settings › Audio › Saved voices.** See everyone you've named, how many voiceprints are stored for each, rename them, or remove them — without waiting for that person to speak again
- Removing a saved voice now takes effect immediately instead of lingering until the app restarts

## r337 — 2026-07-30

- Three settings moved to the tab that actually owns them: context usage now sits beside the system prompt on API (it travels in the same request and dies with the same translators), the chatbox output format moved to VRChat, and live preview moved to General — it only affects the app's own chat log, never VRChat
- "Live preview" is now "Show my message while it translates", which is what it does
- The VRChat chatbox setting is now called "Loopback — send their speech to the VRChat chatbox", matching the LOOPBACK button on the dashboard. They were always the same switch under two different names
- "Caption location" is "Overlay mode" again — it picks VR or Desktop, which is a mode, not a place

## r336 — 2026-07-30

- **Removed clipboard auto-translate.** With it on, anything you copied was translated and posted to the VRChat chatbox — a password or card number copied from a browser went out to everyone nearby. It was off by default; the setting is now gone entirely
- Settings that your current provider ignores are greyed out and say so, naming the provider: with Google Translate, DeepL, Bing or Papago the system prompt and context are never sent, and custom vocabulary only works with Deepgram, Soniox and Qwen ASR 0.6B (Local)
- The custom vocabulary tip was wrong — it left out the local Qwen model, which is the default. It now also tells you terms are stored per language and how many each engine accepts (50, or 12 for the local model)
- The Prompt tab is gone: the system prompt moved under the API tab's model pickers and custom vocabulary under Audio's speech engine, so each sits with the choice that decides whether it does anything
- Settings names use the standard terms again — Mic sensitivity, Mic auto-gain, Mic noise suppression, Audio host, Custom vocabulary — instead of the plain-English rewrites from r335

## r335 — 2026-07-30

- Settings labels now say what each setting does, so you don't have to hover the ⓘ to find out — "Mute Sync" is "Stop listening when you mute in VRChat", "Offset X" is "Move left / right", and so on across every tab
- Jargon is gone from the settings: no more VAD, Host API, Loopback, Single Turn or Intercept
- "Changelog" in the version menu now opens the changelog itself instead of just the info page
- The info page's "What's new" button and window are called "Changelog" too

## r334 — 2026-07-30

- Settings is now split into six tabs — General, Audio, VRChat, API, Prompt, Overlay — instead of piling everything into General
- Audio groups your devices, voice detection and voice processing under labelled headings, so mic setup is one tab instead of a scroll hunt
- VRChat collects the game integration switches (mic gate, push-to-talk sync, live preview, chatbox sending, SteamVR auto-launch)
- "Show my messages in the overlay" was two separate switches in two tabs that could disagree with each other; it is one switch now, in Overlay
- The version menu lists "Changelog" first, then "Check for updates"

## r333 — 2026-07-30

- The what's-new card now shows the build's release date, so it's clear when the version actually came out even if you update days later
- Tighter card: it hugs the text instead of padding to a fixed height, so there's no empty space under the last line
- The version menu's second entry is now "Changelog" and opens the full history of every build (the same list as in Settings), instead of only the newest one
- Clearer opt-out wording again: "Don't show what's new on startup"

## r332 — 2026-07-30

- The app name and version in the top-left is now a menu: click it for "Check for updates" and "What's new" — the update actions now sit next to the version they act on
- Clearer wording on the opt-out in the what's-new dialog: "Don't show what's new on new versions" (it used to say "Don't show this after updates")

## r331 — 2026-07-30

- The what's-new dialog no longer cuts off longer entries — its height now follows the actual wrapped text (Chinese and Japanese included)
- You can click outside the dialog to dismiss it, and there's a "Don't show this after updates" checkbox in the dialog itself. The same switch lives in Settings ("Show what's new after updates") if you want it back
- If an update's file is still uploading when your app tries to fetch it, the app now waits and retries by itself (up to three times) instead of showing a red failure

## r330 — 2026-07-30

- A named voice can now be recognized across different apps: the same person sounds measurably different through a voice call versus VRChat's in-game audio (different codec, plus distance and room effects), so one saved voiceprint often failed to match in the other place and you had to name them again every session. Each person can now hold several voiceprints — naming someone you already named simply teaches the app how they sound there, instead of averaging the two into something that matched neither. Existing saved voices are kept as-is

## r329 — 2026-07-30

- The what's-new dialog is now a small, clean card instead of a huge panel: about a third of the width, plain "What's new in rNNN" heading, tighter text, and a single Close button in the corner

## r328 — 2026-07-30

- The what's-new dialog can finally open: the dialog component required two buttons and the update announcement only has Close, so it crashed at the moment of display on every launch (visible only as a log line). Single-button dialogs are now supported — this also quietly fixes the local-speech-recognition advisory, which had the same latent crash
- The update marker is now saved before the dialog is shown, so a display problem can never again put the announcement into a fail-forever loop

## r327 — 2026-07-30

- Fixed automatic updates never actually running: the launch update check, the retry logic, and the after-update dialog were all accidentally placed inside a disabled leftover code branch from this build's testing days — every update so far only happened when you clicked "Check for updates" yourself. The whole update system now genuinely runs at launch: check, auto-download, auto-apply, and the what's-new dialog. A safeguard test now prevents update code from ever landing in that dead branch again

## r326 — 2026-07-30

- HOTFIX: r325 crashed at every launch ("'GuiController' object has no attribute 'settings_created_fresh'") — the r325 fix set a flag on a class that requires fields to be declared. If you downloaded r325, update to r326 (the app cannot update itself while it cannot start — re-run the installer or grab the zip)

## r325 — 2026-07-30

- Fixed the after-update dialog not appearing the very first time you arrive from an older build: the "previous version" marker didn't exist yet, so the app couldn't tell it had just updated and stayed silent. Anyone updating from r321 or older now sees the what's-new dialog on their first launch of a new build (fresh installs still start quietly)

## r324 — 2026-07-30

- The changelog is now multilingual: switch the UI language and the What's New panel and the after-update dialog immediately show the changes in your language — Chinese, Japanese, and Korean cover everything from r298 onward; older history falls back to English

## r323 — 2026-07-30

- The after-update announcement is now a proper dialog: "PuriPulyHeart+ updated to rNNN — what's new" with the full list of changes and a Close button, instead of a small toast that slid away

## r322 — 2026-07-30

- After a self-update, the app now tells you: a one-time notice on the next launch shows the new build number and the top changes — no more silent updates
- The launch update check now retries several times over the first 10 minutes if it fails or finds nothing (previously ONE attempt, then nothing for 2 hours — a badly-timed check meant the app quietly stayed outdated all session)
- Chat lines with an identified speaker now use the name as the header: "Alex 02:14" instead of "Received · Alex 02:14" (the color still shows the direction)

## r321 — 2026-07-30

- Naming a voice now behaves the way you'd expect: every line already in the chat updates from "Speaker N" to the name immediately, reopening the dialog shows the saved name instead of an empty box, and a just-named voice can no longer slip back to "Speaker N" on borderline matches for the rest of the session. (The name itself was always being saved — only the feedback was missing)

## r320 — 2026-07-30

- The overlay's pinyin and Chinese lines now get the same no-orphan treatment the translation line got in r317: a line that barely overflows shrinks slightly onto one line, and a genuinely long line splits into balanced halves instead of leaving one or two characters alone at the bottom ("...变回 / 来了。"). Covers both pinyin-over-character and block layouts

## r319 — 2026-07-30

- HOTFIX: r318's speaker tags never actually appeared on a normal launch — the voice-matching registry was only hooked up when a language or provider setting changed, not at session start. Voiceprints were being computed the whole time; they just had nothing to match against. Tags now work from the first utterance

## r318 — 2026-07-30

- New: speaker identification. Incoming voices are tagged in the chat as "Speaker 1", "Speaker 2", ... so you can tell people apart in a group call — and clicking a tag lets you NAME a voice you know; named voices are recognized again in future sessions. Voiceprints are stored only on this PC (a small voices.json next to your settings) and are never uploaded anywhere. Settings toggle: "Identify speakers" (on by default). Very short utterances stay untagged (too little audio for a reliable voiceprint), and two similar voices over a compressed call can occasionally be confused

## r317 — 2026-07-30

- The app now updates itself at launch: when the automatic download finishes within the first 10 minutes, it restarts straight into the new build (your session settings restore themselves) — so launching the app means launching the newest version. Updates found later in a session still wait for you to press the restart button. The existing "auto-download updates" setting turns this off
- The update check now works from mainland China: when GitHub's API is blocked, the app reads the version info through the jsDelivr mirror instead, so you still see that an update exists and what changed
- Overlay captions no longer leave one or two words dangling on their own line: a line that barely overflows shrinks slightly to fit on one line, and genuinely long lines split into balanced halves at natural breaks (Chinese lines never start with closing punctuation)

## r316 — 2026-07-29

- The overlay no longer blocks mouse clicks in its screen area during startup: the brief "overlay active" banner required the window to be interactive to display at all, eating clicks for a few seconds on every launch. The banner is retired on locked starts — the overlay is click-through from its very first frame, and the dashboard button already shows that it's active

## r315 — 2026-07-29

- Fixed the overlay silently going invisible while still showing as active: a rare startup timing collision (first caption arriving mid-resize) could kill the overlay's message reader without any error — window stayed on screen, connection stayed "connected", but nothing rendered again until a manual toggle. The overlay now watches for the app's once-a-second heartbeat and restarts itself within 30 seconds if traffic stops, the silent failure path now logs and restarts instead of continuing headless, and the app side detects a dead overlay connection instead of reporting it connected forever

## r314 — 2026-07-29

- The Microphone test percentage now uses a decibel scale like other voice apps: normal speech reads around 50-60%, shouting near the top, and 100% means actual clipping. Previously 100% was raw digital maximum, so even a loud, healthy mic never showed more than single digits — making working mics look broken

## r313 — 2026-07-28

- The standard log now answers audio-plumbing questions by itself (no settings to enable): which capture device each channel actually opened (name, rate, channels), the loudness of every segment sent to speech recognition, a per-minute "pace" check that exposes devices delivering a different sample rate than they claim, and every 2 minutes a survey of ALL output devices' live levels — so a log directly shows when the call is playing through a device the app isn't listening to

## r312 — 2026-07-28

- Four more noise-hallucination patterns are now caught before they reach chat or VRChat (all seen live in a user's log): number walls like "# 2".."# 27" sent as one message, stock phrases with numbers attached ("...的答案是：100"), the same phrase repeated back-to-back ("格力空调，格力空调"), and long template/recursive loops ("这个角色的身高和体重的比是1.75:60" x8, the recursive Xiaoming-story). Real sentences that merely contain these fragments are unaffected

## r311 — 2026-07-28

- The "local speech recognition isn't working" warning no longer recommends Deepgram or links an external GitHub guide (both belonged to the original upstream project, not this fork). It now explains the actual cause — noise or silence reaching the model — and what to check: the Windows default microphone, the device PEER listens to (remote desktop tools and virtual audio devices are common culprits), and the Mic noise suppression setting

## r310 — 2026-07-28

- REVERTED r308: telling the speech model which language to expect makes it TRANSLATE short phrases instead of transcribing them (Japanese audio came out as English sentences, English audio came out as Chinese). Long clear sentences hid the problem, short ones exposed it. Incoming speech is transcribed in whatever language it was actually spoken again

## r309 — 2026-07-28

- With translation OFF, incoming speech is no longer hidden for being "the wrong language" — you now see everything that was heard, in whatever language it was spoken. Filtering by language only applies while you are actually translating
- Received lines are tagged with the detected language ([EN], [ZH], ...) whenever any language can arrive — that is, with translation off or voice auto-detect on — so you can always tell what the recognizer thought it heard
- The Translation card's tooltip now spells this out while translation is off

## r308 — 2026-07-28

- Incoming English (or any language) should stop arriving as Chinese with the local Qwen model: when you have set what language your partners speak, that choice is now passed to the recognizer so it stops guessing. Guessing is what produced fluent-looking Chinese from English speech on noisy game audio. "Auto Detect" still lets the model decide on its own

## r307 — 2026-07-28

- Fixed runaway "怪怪怪怪…" style messages reaching the chat: the repetition detector only recognised loops that started at the very beginning of a line, so anything with a few normal characters in front slipped through
- The local speech model's stock filler on silence ("的答案", "虚构", …) is now treated as the hallucination it is, on both the mic and incoming voice

## r306 — 2026-07-28

- Corrected the dates shown in this list: r304 and r305 were stamped 07-27 but were built on 07-28

## r305 — 2026-07-28

- New "Mic auto-gain" (Settings, ON by default): a quiet microphone is boosted to a stable internal level before speech recognition, so low mic volume no longer makes your speech go unheard. What others hear is unchanged
- Near-silent audio no longer turns into invented text: when a segment is essentially silence, the recognizer's output must be much more confident to be accepted — this kills the stray "虚构"/"的答案是" style junk at the source instead of blocklisting phrases

## r304 — 2026-07-28

- Fixed "Overlay startup timed out": the 3-second limit was too tight — the first launch after an update (while Windows scans the new files) routinely exceeded it. Now 20 seconds
- Fixed r303 being too strict: the "Target language" field also sets what language your PARTNER speaks, so your own language is selectable there again (an English user talking to English speakers needs it). Same-language blocking still applies to real translation pairs

## r303 — 2026-07-27

- Language pickers now grey out the other half of the current pair (per tab), so pointless same-language setups like English -> English or Chinese -> Chinese can't be selected — applies to Your language, Target language, extra targets, and the peer reading language

## r302 — 2026-07-27

- Fixed peer capture getting stuck on silent headphones while in SteamVR: when the capture device is on "default" and goes silent, the app now checks the other output devices for actual sound and follows it (SteamVR plays through the HMD without changing the Windows default — the capture used to sit on the headphones forever). Works both directions when you enter/leave VR

## r301 — 2026-07-27

- Fixed "Mic noise suppression" and "Peer volume auto-gain" showing "Fast Response"/"Stable" instead of On/Off — those labels belong to the low-latency setting and were reused by mistake, making the new toggles unreadable

## r300 — 2026-07-27

- HOTFIX: r299 crashed at launch ("No such file or directory: ...qwen3-asr-0.6b-int8-sherpa.manifest.json") — a cleanup accidentally removed the speech model manifest from the build. If you downloaded r299, update to r300 (or reinstall)
- The build now refuses to package without that manifest, so this cannot ship again

## r299 — 2026-07-27

- New "Peer volume auto-gain" (Settings, ON by default): quiet incoming desktop audio is boosted to a stable internal level before voice detection, so low Windows volume or communications ducking no longer fragments songs/speech into one-word pieces. Playback volume is untouched — you hear no difference

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
