"""Control the app from the VRChat avatar's expression menu over OSC.

Add these parameters to the avatar (Expression Parameters + a menu) and the
app follows them; the app also writes its own state back so the menu shows
what is really on:

  Bool  PuriPuly_Talk           your mic (speech recognition)
  Bool  PuriPuly_Listen         translating the other person's voice
  Bool  PuriPuly_Trans          translation on/off
  Bool  PuriPuly_Captions       desktop/VR captions overlay
  Bool  PuriPuly_MuteSync       follow VRChat's mic mute
  Bool  PuriPuly_ChatboxSource  chatbox shows your original text too
  Bool  PuriPuly_LivePreview    translation in the chatbox while talking
  Int   PuriPuly_Preset         language preset (0 = first tab)

Every incoming value is applied as "set to this", never "toggle", so a value
that comes back unchanged does nothing. On top of that, VRChat echoes the
values the app itself just wrote; those echoes are ignored for a short window,
so the app and the avatar can never flip each other back and forth.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

BOOL_PARAMETERS: dict[str, str] = {
    "PuriPuly_Talk": "talk",
    "PuriPuly_Listen": "listen",
    "PuriPuly_Trans": "trans",
    "PuriPuly_Captions": "captions",
    "PuriPuly_MuteSync": "mute_sync",
    "PuriPuly_ChatboxSource": "chatbox_source",
    "PuriPuly_LivePreview": "live_preview",
}
INT_PARAMETERS: dict[str, str] = {
    "PuriPuly_Preset": "preset",
}
PARAMETER_FOR_KEY: dict[str, str] = {
    key: name for name, key in {**BOOL_PARAMETERS, **INT_PARAMETERS}.items()
}
_ECHO_WINDOW_S = 2.0


class AvatarControls:
    def __init__(
        self,
        *,
        get_state: Callable[[str], Any],
        apply: Callable[[str, Any], None],
        send: Callable[[str, Any], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._get_state = get_state
        self._apply = apply
        self._send = send
        self._clock = clock
        self._sent: dict[str, tuple[Any, float]] = {}
        self._published: dict[str, Any] = {}

    @staticmethod
    def _normalize(name: str, value: Any) -> Any:
        if name in BOOL_PARAMETERS:
            if isinstance(value, (int, float, bool)):
                return bool(value)
            return None
        if name in INT_PARAMETERS:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        return None

    def on_parameter(self, name: str, value: Any) -> bool:
        """A value VRChat reported for an avatar parameter. True = applied."""
        key = BOOL_PARAMETERS.get(name) or INT_PARAMETERS.get(name)
        if key is None:
            return False
        normalized = self._normalize(name, value)
        if normalized is None:
            return False
        sent = self._sent.get(name)
        if sent is not None and sent[0] == normalized and self._clock() < sent[1]:
            self._sent.pop(name, None)
            return False  # VRChat repeating what the app just wrote
        try:
            current = self._get_state(key)
        except Exception:
            current = None
        if current is not None and self._normalize(name, current) == normalized:
            return False  # already in that state
        self._published[key] = normalized  # the app now matches the avatar
        self._apply(key, normalized)
        return True

    def publish(self, key: str, value: Any) -> None:
        name = PARAMETER_FOR_KEY.get(key)
        if name is None or value is None:
            return
        normalized = self._normalize(name, value)
        if normalized is None:
            return
        self._sent[name] = (normalized, self._clock() + _ECHO_WINDOW_S)
        self._published[key] = normalized
        self._send(name, normalized)

    def sync(self, *, force: bool = False) -> int:
        """Send every state that changed since the last send (all with force,
        e.g. after the avatar changed). Returns how many were sent."""
        count = 0
        for key in PARAMETER_FOR_KEY:
            try:
                value = self._get_state(key)
            except Exception:
                continue
            if value is None:
                continue
            name = PARAMETER_FOR_KEY[key]
            normalized = self._normalize(name, value)
            if force or self._published.get(key) != normalized:
                self.publish(key, normalized)
                count += 1
        return count
