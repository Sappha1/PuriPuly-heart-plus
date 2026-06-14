# Peer presentation refresh burst as product-permanent mechanism

## Decision

Keep `peer_presentation_refresh_burst` default-on and preserve the
`peer_presentation_refresh=<n>` session-scope nonce as revision-worthy metadata.
This is the Stage 1 product baseline, not a Stage 2 experiment. The burst and
nonce remain product-permanent unless Stage 2 HMD QA proves and approves a
replacement that preserves peer overlay behavior.

## Evidence

- `de4a66d`: fixed the content-aware `damage_band` bug by making layout/content
  changes visible to damage comparison instead of comparing visual bounds alone.
- `e902891`: introduced the peer presentation refresh burst, republishing fresh
  peer snapshots for a bounded window with a changing
  `peer_presentation_refresh=<n>` session-scope nonce.
- `c846eee`: locked peer overlay product behavior to translation arrival rather
  than source-only active speech.
- 2026-04-28 submit-only resubmit regression: automated Python tests, Rust
  tests, and the Windows release build passed, but HMD QA showed the peer N-1
  lag returned when the burst was replaced with repeated stored-frame
  `SetOverlayTexture` resubmission.

## Consequence

The load-bearing mechanism is:

```text
peer translation update
→ visible peer snapshot
→ each burst tick increments peer_presentation_refresh=<n>
→ snapshot revision changes
→ native applies the new snapshot
→ renderer produces fresh frame/GPU work
→ overlay texture is submitted
→ burst end publishes a clean snapshot without the marker
```

The nonce must not be normalized away as cosmetic metadata. Removing it, making
it non-revision-worthy, or replacing it with stored-frame resubmission changes
the mechanism that resolved the HMD-visible lag.

## Rejected alternatives

- Submit-only `resubmit_current_frame` / stored-frame `SetOverlayTexture`
  repetition for this symptom.
- `DEBUG_OVERLAY_TICK`, `overlay_tick`, or any TICK/debug forced redraw as a
  hidden production fix.
- Removing the `peer_presentation_refresh=<n>` session-scope nonce as cosmetic.
- Treating self/peer lifecycle parity as a reason to make peer captions visible
  before translation arrival.
- Stage 1 changes to burst defaults, burst cadence, `damage_band`, D3D11
  `Flush`, render task structure, or submit cadence.

## Stage 2 follow-up

Stage 2 may experimentally test alternatives such as D3D11 `Flush`, burst-off
variants, lower cadence/duration mappings, periodic GPU-pump hypotheses,
`damage_band` isolation, or render-task separation. None of those alternatives
should ship unless normal HMD QA confirms no peer N-1 lag regression, no
source-only peer overlay regression, visible translated peer rows, and continued
diagnostic evidence for timing and refresh behavior.
