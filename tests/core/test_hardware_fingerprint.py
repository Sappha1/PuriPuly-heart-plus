from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest

from puripuly_heart.core import hardware_fingerprint
from puripuly_heart.core.hardware_fingerprint import (
    compute_hardware_hash,
    get_raw_hardware_fingerprint,
)


def _expected_hash(*, fingerprint_salt: str, raw_fingerprint: str) -> str:
    digest = hashlib.sha256(f"{fingerprint_salt}{raw_fingerprint}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def test_compute_hardware_hash_is_stable_for_same_inputs() -> None:
    first = compute_hardware_hash(
        fingerprint_salt="fingerprint-salt-test",
        raw_fingerprint="raw-hardware-fingerprint-test",
    )
    second = compute_hardware_hash(
        fingerprint_salt="fingerprint-salt-test",
        raw_fingerprint="raw-hardware-fingerprint-test",
    )

    assert (
        first
        == second
        == _expected_hash(
            fingerprint_salt="fingerprint-salt-test",
            raw_fingerprint="raw-hardware-fingerprint-test",
        )
    )


def test_compute_hardware_hash_changes_when_salt_changes() -> None:
    baseline = compute_hardware_hash(
        fingerprint_salt="fingerprint-salt-a",
        raw_fingerprint="raw-hardware-fingerprint-test",
    )
    changed = compute_hardware_hash(
        fingerprint_salt="fingerprint-salt-b",
        raw_fingerprint="raw-hardware-fingerprint-test",
    )

    assert baseline != changed


def test_compute_hardware_hash_uses_exact_non_empty_inputs_without_trimming() -> None:
    result = compute_hardware_hash(
        fingerprint_salt=" fingerprint-salt-test ",
        raw_fingerprint="\traw-hardware-fingerprint-test\n",
    )

    assert result == _expected_hash(
        fingerprint_salt=" fingerprint-salt-test ",
        raw_fingerprint="\traw-hardware-fingerprint-test\n",
    )


@pytest.mark.parametrize(
    ("fingerprint_salt", "raw_fingerprint"),
    [
        ("", "raw-hardware-fingerprint-test"),
        ("   ", "raw-hardware-fingerprint-test"),
        ("fingerprint-salt-test", ""),
        ("fingerprint-salt-test", "   "),
    ],
)
def test_compute_hardware_hash_rejects_empty_inputs(
    *, fingerprint_salt: str, raw_fingerprint: str
) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        compute_hardware_hash(
            fingerprint_salt=fingerprint_salt,
            raw_fingerprint=raw_fingerprint,
        )


@pytest.mark.parametrize(
    ("system_name", "expected_value", "reader_name"),
    [
        ("Windows", "machine-guid-123", "_get_windows_raw_hardware_fingerprint"),
        ("Linux", "linux-machine-id-123", "_get_linux_raw_hardware_fingerprint"),
        ("Darwin", "macos-platform-uuid-123", "_get_macos_raw_hardware_fingerprint"),
    ],
)
def test_get_raw_hardware_fingerprint_selects_platform_reader(
    monkeypatch: pytest.MonkeyPatch,
    *,
    system_name: str,
    expected_value: str,
    reader_name: str,
) -> None:
    calls: list[str] = []

    def _make_reader(name: str, value: str):
        def _reader() -> str:
            calls.append(name)
            return value

        return _reader

    monkeypatch.setattr(hardware_fingerprint.platform, "system", lambda: system_name)
    monkeypatch.setattr(
        hardware_fingerprint,
        "_get_windows_raw_hardware_fingerprint",
        _make_reader("windows", "machine-guid-123"),
    )
    monkeypatch.setattr(
        hardware_fingerprint,
        "_get_linux_raw_hardware_fingerprint",
        _make_reader("linux", "linux-machine-id-123"),
    )
    monkeypatch.setattr(
        hardware_fingerprint,
        "_get_macos_raw_hardware_fingerprint",
        _make_reader("macos", "macos-platform-uuid-123"),
    )

    result = get_raw_hardware_fingerprint()

    assert result == expected_value
    assert calls == [reader_name.removeprefix("_get_").removesuffix("_raw_hardware_fingerprint")]


def test_get_raw_hardware_fingerprint_raises_for_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hardware_fingerprint.platform, "system", lambda: "Plan9")

    with pytest.raises(RuntimeError, match="unsupported platform"):
        get_raw_hardware_fingerprint()


def test_linux_reader_raises_when_machine_id_sources_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        hardware_fingerprint,
        "_LINUX_MACHINE_ID_PATHS",
        (tmp_path / "missing-machine-id", tmp_path / "missing-dbus-machine-id"),
    )

    with pytest.raises(RuntimeError, match="machine-id"):
        hardware_fingerprint._get_linux_raw_hardware_fingerprint()


def test_linux_reader_falls_back_to_second_machine_id_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    first_path = tmp_path / "machine-id"
    second_path = tmp_path / "dbus-machine-id"
    first_path.write_text("   ", encoding="utf-8")
    second_path.write_text("linux-machine-id-456\n", encoding="utf-8")
    monkeypatch.setattr(
        hardware_fingerprint,
        "_LINUX_MACHINE_ID_PATHS",
        (first_path, second_path),
    )

    assert hardware_fingerprint._get_linux_raw_hardware_fingerprint() == "linux-machine-id-456"


def test_windows_reader_returns_machine_guid(monkeypatch: pytest.MonkeyPatch) -> None:
    class _KeyContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb) -> None:
            _ = exc_type, exc, tb

    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE=object(),
        OpenKey=lambda root, path: _KeyContext(),
        QueryValueEx=lambda key, name: ("machine-guid-456", 1),
    )
    monkeypatch.setattr(hardware_fingerprint, "winreg", fake_winreg)

    assert hardware_fingerprint._get_windows_raw_hardware_fingerprint() == "machine-guid-456"


def test_macos_reader_parses_platform_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hardware_fingerprint.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='    | "IOPlatformUUID" = "macos-platform-uuid-456"\n',
        ),
    )

    assert hardware_fingerprint._get_macos_raw_hardware_fingerprint() == "macos-platform-uuid-456"


def test_macos_reader_raises_when_ioreg_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hardware_fingerprint.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    with pytest.raises(RuntimeError, match="ioreg failed"):
        hardware_fingerprint._get_macos_raw_hardware_fingerprint()
