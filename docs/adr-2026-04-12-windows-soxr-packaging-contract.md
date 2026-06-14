# ADR: Windows soxr Packaging Contract

- Status: Accepted
- Date: 2026-04-12
- Work ref: `stt-soxr-fast-path-16khz`

## Context

The Windows local-STT fast-path work introduced a custom, system-linked python-soxr runtime pair for packaged builds. During implementation and verification, several durable packaging decisions became necessary:

- the concrete build/link inputs already used the basename `soxr.dll`, while parts of the packaged-runtime validation still expected `libsoxr.dll`
- Windows packaged builds must keep `soxr_ext.pyd` and its sibling DLL together under a stable packaged path
- PyInstaller can auto-collect a duplicate root-level `soxr.dll` even when the intended runtime pair is already staged under `soxr/`
- the full release-complete Windows path must stage LGPL compliance materials in the packaged and installed product, not only in release artifacts
- installer upgrade/reinstall behavior must clean up stale legacy `libsoxr.dll` remnants so the shipped runtime contract remains deterministic

Leaving these choices implicit would make future packaging edits fragile and make it harder to understand why the build, installer, and guards are wired the way they are.

## Decision

### 1. Authoritative Windows runtime pair

The authoritative packaged Windows soxr runtime pair is:

- `dist/PuriPulyHeart/soxr/soxr_ext.pyd`
- `dist/PuriPulyHeart/soxr/soxr.dll`

`soxr.dll` is the accepted packaged/runtime basename. `libsoxr.dll` is not part of the current shipped contract.

### 2. Duplicate root-level `soxr.dll` is forbidden

The final packaged tree must not contain a second root-level `dist/PuriPulyHeart/soxr.dll`.

If PyInstaller bindepend discovers the same DLL as a root-level binary dependency of `soxr_ext.pyd`, the spec file must normalize that TOC entry away before `COLLECT(...)` so the shipped layout remains the sibling pair under `soxr/` only.

### 3. Release-complete Windows packaging path

The release-complete Windows packaging path is:

1. `scripts/ci/prepare-soxr-release-inputs.ps1`
2. `scripts/ci/build-release-artifacts.ps1`

Any workflow that runs `build-release-artifacts.ps1` must run `prepare-soxr-release-inputs.ps1` first.

Direct/manual packaging steps such as `prepare-soxr-release-inputs.ps1` + `pyinstaller build.spec` + `ISCC installer.iss` are not the authoritative release-complete path by themselves. They are only direct/manual packaging steps and still depend on other staged prerequisites and release-script staging behavior.

### 4. Compliance bundle location

The packaged and installed compliance bundle for python-soxr / libsoxr lives under:

- packaged: `dist/PuriPulyHeart/third_party/soxr/`
- installed: `third_party\soxr\`

That bundle must include at least:

- `COPYING.LGPL-2.1.txt`
- `PuriPulyHeart-soxr-third-party-source-bundle.zip`

`THIRD_PARTY_NOTICES.txt` must describe this bundle using the literal user-facing relative path `third_party\soxr\`, not installer-only placeholder wording such as `{app}`.

### 5. Upgrade / reinstall behavior

Installer and reinstall flows must remove stale legacy `soxr/libsoxr.dll` remnants and restore the official bundled runtime/compliance files.

The current product does not preserve user-replaced soxr runtime DLLs across reinstall.

## Consequences

- runtime helpers, smoke checks, installer checks, and tests should target `soxr.dll`, not `libsoxr.dll`
- `build.spec`, `scripts/ci/build-release-artifacts.ps1`, `installer.iss`, and release guard tests form a single contract surface and must be updated together when this layout changes
- release verification must check both packaged and installed trees for the runtime pair, compliance bundle, and absence of stale `libsoxr.dll`
- documentation must clearly distinguish direct/manual packaging from the release-complete Windows path

## Alternatives Considered

### Keep `libsoxr.dll` as the packaged basename

Rejected because the concrete Windows build/link path already produced and consumed `soxr.dll`. Keeping `libsoxr.dll` at the packaged-runtime layer would have required reworking the wheel/build side instead of aligning packaging with the actual runtime inputs.

### Allow both root-level `soxr.dll` and `soxr/soxr.dll`

Rejected because duplicate destinations create an ambiguous shipped layout and weaken the sibling-DLL contract that runtime checks and installer cleanup rely on.

### Treat direct `pyinstaller build.spec` + `ISCC installer.iss` as the authoritative release path

Rejected because the compliance bundle and release smoke guarantees are finalized by `scripts/ci/build-release-artifacts.ps1`, not by the direct/manual packaging steps alone.

## References

- `docs/superpowers/plans/2026-04-12-stt-soxr-release-hardening-implementation.md`
- `agents/logs/2026-04-12-stt-soxr-release-hardening-verification.md`
