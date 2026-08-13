from __future__ import annotations

import ntpath
from dataclasses import dataclass
from typing import Final, Literal

_DISCORD_BASENAME_BY_CHANNEL: Final[dict[str, str]] = {
    "stable": "Discord.exe",
    "ptb": "DiscordPTB.exe",
    "canary": "DiscordCanary.exe",
}
_DISCORD_BASENAME_BY_CHANNEL_CASEFOLDED: Final[frozenset[str]] = frozenset(
    basename.casefold() for basename in _DISCORD_BASENAME_BY_CHANNEL.values()
)


def _normalize_executable_identity(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("process executable identity must be non-empty")
    normalized = ntpath.normcase(ntpath.normpath(value.strip().replace("/", "\\")))
    if (
        not normalized
        or normalized in (".", "\\")
        or not _is_drive_qualified_absolute_windows_path(normalized)
        or not ntpath.basename(normalized).casefold().endswith(".exe")
    ):
        raise ValueError("process executable identity must name an executable")
    return normalized


def _is_drive_qualified_absolute_windows_path(value: str) -> bool:
    if value.startswith(("\\\\.\\", "\\\\?\\", "\\??\\", "\\Device\\")):
        return False
    drive, tail = ntpath.splitdrive(value)
    is_drive_qualified = (
        len(drive) == 2 and drive[0].isalpha() and drive[1] == ":" and tail.startswith("\\")
    )
    unc_root = drive[2:].split("\\") if drive.startswith("\\\\") else ()
    is_fully_qualified_unc = len(unc_root) == 2 and all(unc_root) and tail.startswith("\\")
    return is_drive_qualified or is_fully_qualified_unc


def _normalize_discord_channel(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Discord target channel must be a string")
    channel = value.strip().casefold()
    if channel not in _DISCORD_BASENAME_BY_CHANNEL:
        raise ValueError("unsupported Discord target channel")
    return channel


@dataclass(frozen=True, slots=True)
class ProcessCaptureTargetIntent:
    kind: Literal["generic_executable", "vrchat", "discord"]
    executable_identity: str | None = None
    discord_channel: str | None = None
    executable_basename: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "generic_executable":
            identity = _normalize_executable_identity(self.executable_identity)
            if self.discord_channel is not None or self.executable_basename is not None:
                raise ValueError("generic executable targets cannot include Discord identity")
            if ntpath.basename(identity).casefold() in _DISCORD_BASENAME_BY_CHANNEL_CASEFOLDED:
                raise ValueError("Discord targets must use a Discord channel identity")
            object.__setattr__(self, "executable_identity", identity)
            return
        if self.kind == "vrchat":
            identity = _normalize_executable_identity(self.executable_identity)
            if ntpath.basename(identity).casefold() != "vrchat.exe":
                raise ValueError("VRChat targets must identify VRChat.exe")
            if self.discord_channel is not None or self.executable_basename is not None:
                raise ValueError("VRChat targets cannot include Discord identity")
            object.__setattr__(self, "executable_identity", identity)
            return
        if self.kind == "discord":
            channel = _normalize_discord_channel(self.discord_channel)
            basename = _DISCORD_BASENAME_BY_CHANNEL[channel]
            if self.executable_identity is not None:
                raise ValueError("Discord targets cannot persist an installation path")
            if self.executable_basename not in (None, basename):
                raise ValueError("Discord target basename does not match its channel")
            object.__setattr__(self, "discord_channel", channel)
            object.__setattr__(self, "executable_basename", basename)
            return
        raise ValueError(f"unsupported process capture target kind: {self.kind}")

    @classmethod
    def generic_executable(cls, executable_identity: str) -> ProcessCaptureTargetIntent:
        return cls(kind="generic_executable", executable_identity=executable_identity)

    @classmethod
    def vrchat(cls, executable_identity: str) -> ProcessCaptureTargetIntent:
        return cls(kind="vrchat", executable_identity=executable_identity)

    @classmethod
    def discord(cls, channel: str) -> ProcessCaptureTargetIntent:
        return cls(kind="discord", discord_channel=channel)


# ── persistence inside the existing output_device string ─────────────────────
# A process target rides in desktop_audio.output_device as "process:...", so
# presets/sync/favorites that copy the string around keep working unchanged.
# Real device names never start with this prefix.
PROCESS_CAPTURE_OPTION_PREFIX = "process:"


def serialize_process_capture_target(target: ProcessCaptureTargetIntent) -> str:
    if target.kind == "discord":
        return f"process:discord:{target.discord_channel}"
    if target.kind == "vrchat":
        return f"process:vrchat:{target.executable_identity}"
    return f"process:generic:{target.executable_identity}"


def process_capture_display_name(value: str | None) -> str | None:
    """Short human name for a process option string, None for device values."""
    try:
        target = parse_process_capture_target(value)
    except ValueError:
        return None
    if target is None:
        return None
    if target.kind == "vrchat":
        return "VRChat"
    if target.kind == "discord":
        channel = target.discord_channel or "stable"
        return "Discord" if channel == "stable" else f"Discord ({channel.upper()})"
    basename = ntpath.basename(target.executable_identity or "")
    return basename or "app"


def parse_process_capture_target(value: str | None) -> ProcessCaptureTargetIntent | None:
    """Return the intent encoded in an output_device string, or None for plain
    device names / blanks. Malformed process strings raise ValueError — a
    saved process target must never silently degrade to device loopback."""
    text = (value or "").strip()
    if not text.startswith(PROCESS_CAPTURE_OPTION_PREFIX):
        return None
    rest = text[len(PROCESS_CAPTURE_OPTION_PREFIX):]
    kind, sep, payload = rest.partition(":")
    if not sep or not payload:
        raise ValueError(f"malformed process capture option: {text!r}")
    if kind == "discord":
        return ProcessCaptureTargetIntent.discord(payload)
    if kind == "vrchat":
        return ProcessCaptureTargetIntent.vrchat(payload)
    if kind == "generic":
        return ProcessCaptureTargetIntent.generic_executable(payload)
    raise ValueError(f"unsupported process capture option kind: {kind!r}")
