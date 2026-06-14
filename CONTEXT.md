# PuriPuly

PuriPuly is a two-way conversation translator for VRChat. It turns spoken turns into natural localized text for the user's own speech and, when enabled, another participant's speech.

## Language

**Spoken Turn**:
A continuous span of speech from one Channel as experienced by the speaker. A long Spoken Turn may be divided into multiple Utterance Segments for display and translation flow.
_Avoid_: sentence, message, audio clip

**Utterance Segment**:
A bounded processing unit that PuriPuly follows from speech detection through transcript, translation, and output. Most Spoken Turns produce one Utterance Segment; long continuous Spoken Turns may produce several.
_Avoid_: sentence, message, audio clip

**Transcript**:
Text recognized from speech before it is translated.
_Avoid_: transcription result, STT text

**Translation**:
Localized text produced from a transcript for the configured target language while preserving conversational tone.
_Avoid_: response, output text

**Speech Recognition Hint**:
A user-supplied word or phrase that may bias speech recognition for the user's own Channel and selected source language. It is not a transcript correction, a translation glossary, or a guaranteed rewrite.
_Avoid_: custom vocabulary, tag, prompt term

**Channel**:
The side of a conversation that a Utterance Segment belongs to: either the user's own speech path or another participant's speech path when peer voice translation is enabled.
_Avoid_: stream, role, side, local user, remote user

**Content Language**:
The language of a displayed Transcript or Translation line, used when presentation must respect the language of the text itself.
_Avoid_: UI language, system locale, font language

**Context Memory**:
Recent conversation history that can be supplied to translation so nearby turns influence wording and tone.
_Avoid_: chat history, memory cache

**Translation Connection**:
The way PuriPuly obtains translation access for the selected translation model, such as managed access, a user's own provider account, or a local compatible endpoint.
_Avoid_: provider mode, API mode

**Managed Connection**:
A PuriPuly-managed translation connection that lets eligible users start through Discord authentication instead of bringing their own API key.
_Avoid_: free mode, trial key, hosted translation

**User-Owned Cloud Connection**:
A cloud Translation Connection where the user supplies their own provider credential instead of using PuriPuly-managed access. This does not include local compatible endpoints.
_Avoid_: BYOK mode, personal mode

**Broker**:
The PuriPuly-controlled authority for managed eligibility and credential issuance. The broker is not the translation provider and does not translate speech.
_Avoid_: translation proxy, OpenRouter proxy

**Talk Together Pass ID**:
A shareable pass identifier that appears after Discord verification and can be shared with a friend for extra managed usage together.
_Avoid_: referral code, invite code, pass code

**VR Subtitle Overlay**:
The in-VR display surface used to show transcripts and translations without relying only on the VRChat chatbox.
_Avoid_: overlay app, subtitle window

**Desktop Subtitle Overlay**:
The desktop screen display surface used to show transcripts and translations outside the VR headset.
_Avoid_: desktop renderer, Flet overlay, subtitle window

## Flagged ambiguities

- Use **Managed Connection** for the user-facing connection mode, **Managed Key** for the issued credential surface, and **Talk Together Pass ID** for the shareable invite identifier.
- Use **User-Owned Cloud Connection** when the user supplies a cloud provider credential. Do not use it for local compatible endpoints.
- Use **Broker** only for managed eligibility and credential issuance. Do not call it a translation proxy.

## Example dialogue

Developer: "Should a peer-channel Utterance Segment be sent to the VRChat chatbox?"

Domain expert: "No. A peer-channel Utterance Segment represents another participant's speech. Its transcript and translation belong in the VR Subtitle Overlay; the VRChat chatbox is for sending the user's own translated speech into VRChat."

Developer: "If a new user chooses Managed Connection, do they need an OpenRouter API key first?"

Domain expert: "No. Managed Connection starts through Discord verification. If managed access succeeds, the broker issues managed translation access; users bring their own key only when they choose a non-managed Translation Connection."
