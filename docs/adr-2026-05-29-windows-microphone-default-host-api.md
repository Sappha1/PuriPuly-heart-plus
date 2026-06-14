# ADR: Windows Microphone Default Host API Policy

- Status: Accepted
- Date: 2026-05-29
- Work ref: `microphone-test-host-api`

## Context

The microphone-test and Host API work added manual choices for Auto, Windows WASAPI,
Windows WASAPI Compatibility Mode, Windows DirectSound, and MME. The initial branch
defaulted new and missing microphone Host API settings to Auto, which delegates to
PortAudio's system default input route. On the current Windows test machine that
default route resolves through MME.

That creates an ambiguous product policy: MME remains useful as a legacy fallback,
especially for some VR or virtual microphone setups, but it should not become the
implicit first-run recommendation when a more modern WASAPI shared-mode compatibility
profile is available.

## Decision

New settings and settings that omit `audio.input_host_api` default to:

```text
Windows WASAPI (Compatibility Mode)
```

This profile opens the real `Windows WASAPI` Host API with:

```text
wasapi_exclusive = False
wasapi_auto_convert = True
```

Explicit saved user choices remain authoritative:

- explicit blank `""` continues to mean Auto / system default
- explicit `Windows WASAPI` remains normal WASAPI
- explicit `Windows DirectSound` remains DirectSound
- explicit `MME` remains MME
- unknown strings continue loading as strings and fail gracefully at runtime

MME remains a visible manual fallback when PortAudio reports it, but it is no
longer reached implicitly by the new-user default through Auto.

## Consequences

- first-run microphone capture starts with a modern Windows audio path while
  allowing system-level conversion for sample-rate/channel mismatches
- users can still manually select Auto if they want PortAudio's system default
  route, including machines where that route is MME
- guided microphone onboarding should test in this order when implemented:
  WASAPI, WASAPI Compatibility Mode, DirectSound, then MME
- support logs and the microphone test remain important because no single Host
  API is guaranteed to be best for all VR, streaming, or virtual-audio setups

## Alternatives Considered

### Keep Auto as the default

Rejected because Auto can resolve to MME on Windows, making a legacy fallback act
like the implicit default even though the saved setting only says Auto.

### Make normal WASAPI the default

Rejected because normal WASAPI is less forgiving of sample-rate/channel mismatch
than the compatibility profile, which is the problem this default should reduce.

### Make MME the explicit default

Rejected because MME is not generally a better Windows microphone path. It is a
legacy fallback that may hide format or buffer problems in some environments.

## References

- `src/puripuly_heart/config/audio_host_api.py`
- `src/puripuly_heart/config/settings.py`
- `tests/config/test_config_and_secrets.py`
