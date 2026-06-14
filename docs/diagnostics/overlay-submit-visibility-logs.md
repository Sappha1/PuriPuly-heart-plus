# Overlay Submit / Visibility Logs

## Purpose

This note documents the self-overlay diagnostics added to narrow the last two unknown boundaries in overlay investigations:

- whether the native overlay reached successful `submit_frame()` after `frame_rendered`
- how long a self translation row actually stayed visible before it was cleared

These logs do not change overlay behavior. They only improve observability.

## New Rust Runtime Log

The native overlay child now emits a `frame_submitted` line after `submit_frame()` succeeds for self-related frames.

Example:

```text
[overlay][INFO] frame_submitted revision=648 visible_block_count=1 self_block_count=1 fully_transparent=false overlay_visible_before=true overlay_visible_after=true should_show_after_submit=false
```

Fields:

- `revision`: snapshot revision that was successfully submitted to OpenVR
- `visible_block_count`: number of visible blocks in the rendered layout
- `self_block_count`: number of visible self blocks in that submitted frame
- `fully_transparent`: whether the submitted frame had no visible content
- `overlay_visible_before`: runtime visibility flag before this submit
- `overlay_visible_after`: runtime visibility flag after this submit
- `should_show_after_submit`: whether this submit caused the runtime to request `visible=true`

Logging rule:

- self frames are always logged
- empty clear frames are also logged when the immediately previous submitted frame contained self content
- peer-only frames are not covered by this log

This separates:

- `frame_rendered`: layout/render completed locally
- `frame_submitted`: OpenVR submit completed successfully

## Expanded Presenter Removal Diagnostics

Self `entry_removed` diagnostics now include visibility-duration fields.

Example shape:

```json
{
  "event": "entry_removed",
  "reason": "expired",
  "channel": "self",
  "lifetime_ms": 11100.0,
  "translated_lifetime_ms": 4100.0,
  "had_translation": true,
  "ever_visible_with_translation": true
}
```

Added self-only fields:

- `lifetime_ms`: milliseconds from `visible_since` to removal
- `translated_lifetime_ms`: milliseconds from `translation_visible_since` to removal, or `0` if no translation ever became visible
- `had_translation`: whether the row still held translation text at removal time
- `ever_visible_with_translation`: whether a translated state ever became visible on-screen

Existing removal fields are unchanged:

- `reason`
- `visible_deadline`
- `translation_deadline`
- `effective_deadline`
- `visible_since`
- `translation_visible_since`
- `closed_at`

Peer removal diagnostics keep their current meaning and do not receive these self-only fields.

## How To Read A Repro

Use this order when the user says "chatbox had the translation but overlay looked like it never attached":

1. Check `active_self_secondary` and `overlay_emit`
   - confirms whether Python chose a secondary string and attempted overlay delivery
2. Check `frame_rendered`
   - confirms the native runtime built a layout with `secondary_present=true` or not
3. Check `frame_submitted`
   - confirms that rendered frame was actually submitted successfully
4. Check self `entry_removed`
   - confirms how long the translated row stayed visible before it disappeared

Interpretation shortcuts:

- `frame_rendered` exists but no `frame_submitted`
  - render/layout happened, but submit did not complete successfully
- `frame_submitted` exists and `translated_lifetime_ms` is very small
  - translation attached, but the row did not stay on-screen long enough to be perceived reliably
- `frame_submitted` exists and `translated_lifetime_ms` is healthy
  - the failure is likely after submit or in user perception of the burst between active/finalized states

## Two Common Examples

### 1. Submit succeeded, but translated lifetime was short

Pattern:

- `overlay_emit event_kind=translation_final`
- `frame_rendered ... secondary_present=true`
- `frame_submitted ... self_block_count=1`
- `entry_removed ... translated_lifetime_ms=120.0`

Meaning:

The translation did reach the overlay and submit succeeded, but it only remained visible for about 120 ms before the row was cleared.

### 2. Render and submit disagree across a burst

Pattern:

- `frame_rendered` for `active_self` with translation
- `frame_submitted` for that active revision
- immediate finalized revision with `secondary_present=false`
- later finalized revision with `secondary_present=true`

Meaning:

The system is transitioning through multiple revisions fast enough that the user may perceive "not attached" even though data existed in multiple intermediate states. The logs now make that burst explicit instead of inferring it from OSC/chatbox timing.
