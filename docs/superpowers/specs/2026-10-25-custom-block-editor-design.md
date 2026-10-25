# Custom mode block editor

## Intent and agreed behavior

Expose the three custom schedule blocks in Home Assistant for H7102 and H7126.
Users edit the blocks locally, then press **Apply custom mode** to send the
complete schedule. Applying selects Custom, including when another mode was
previously active. Restoring the previous mode is not required.

## Approaches

- Immediate writes from each control: simple, but activates Custom repeatedly
  while users are still editing and sends a complete schedule per edit.
- Staged controls and an Apply button: users can finish several edits before a
  single schedule submission. The user selected this approach.
- An automation-only action: fewer entities, but does not offer the requested
  individual block controls through the normal device page.

## Controls

Use standard HA entities, with stable identifiers per device, block, and field.
Register number and select platforms alongside the existing platforms.

| Model | Controls for each of blocks 1, 2, and 3 |
| --- | --- |
| H7102 | Speed number (1–8), oscillation switch; duration in whole minutes for blocks 1 and 2 |
| H7126 | Mode select (Sleep, Low, High); duration in whole minutes for blocks 1 and 2 |

Both models have an **Apply custom mode** button. Blocks 1 and 2 use finite
whole-minute durations from 1–65534. Block 3 always runs continuously: it has no
duration entity or Continuous switch. Apply always encodes its Continuous
sentinel, even if fresh readback contains a finite final duration. Old draft
fields for block 3 duration/Continuous are ignored on load; their obsolete
entities are removed from this integration's entity registry on successful setup.

Editing controls performs no BLE writes and does not change the fan entity's
reported active mode. The existing fan Custom preset continues to select the
schedule currently saved on the device; it does not implicitly apply draft edits.
H5179 gains no schedule entities.

## Drafts and device observations

Implementation choices proposed by the author:

- Keep a per-device draft containing only edited fields. Persist it with HA's versioned Store helper so unsubmitted
  edits survive reloads/restarts. Keep storage separate from connection settings.
- Read the device's schedule during ordinary refreshes for supported models.
  Schedule retrieval is optional for general device availability: missing or
  malformed schedule replies must not discard valid power or sensor readings.
  Bound response collection and retain the existing notification limits.
- Display edited fields from the draft and unedited fields from the latest valid
  schedule. Polling must not overwrite pending edits. Do not invent a complete
  default schedule when none has been read.
- Draft edits are local settings and remain editable after initialization even
  if the device becomes temporarily unavailable. Before initialization, controls
  without a known value remain unavailable. Apply requires a reachable device
  and at least one valid schedule observation; it must still perform fresh
  preflight reads rather than trusting that observation.
- Store no raw packets or encryption data in the persisted draft. Validate
  persisted values before use and isolate storage by config entry. Remove its
  storage when the config entry is removed.

## Apply transaction

All radio work uses the existing coordinator transaction admission, session
ownership, timeouts, notification bounds, cleanup, and cancellation behavior.
Applying is a control barrier and must not coalesce with queued speed changes.

1. Snapshot the edited fields and their revision when Apply is requested.
2. Under one serialized transaction, read the current saved schedule and power
   state. Merge only the snapshotted edits into that fresh schedule. This preserves
   unedited blocks and incorporates changes made through the Govee app.
3. Validate the complete resulting schedule before sending any controls. Fail
   without controls if the schedule cannot be read or represented safely.
4. If the device is off, power it on, matching the existing Custom preset's
   behavior. Submit the complete edited schedule once; this selects Custom.
   Preserve existing model-specific pacing and settle behavior.
5. Read back the saved schedule and active mode with bounded queries. Confirm
   the submitted fields match and the active mode is Custom. A completed
   write alone does not establish confirmation.
6. On confirmed success, update observed schedule/mode state and clear only
   the draft edits included in this submission that have not since changed.
   Edits made while Apply was in progress remain pending. Persist draft changes.

With no pending edits, Apply intentionally activates the freshly read saved
schedule, providing consistent behavior with its label. Serialize repeated Apply
requests, but each explicit press may submit its own snapshot; never retry an
uncertain write automatically.

## Preservation and failures

H7102 exposes a raw settings writer. Preserve its leading marker, reserved speed
bits, and all untouched bytes. Change only the documented speed nibble,
oscillation bit, and duration words for edited fields. Unknown duration encodings
may remain untouched; refuse to expose an undecodable finite duration as a valid
number. Explicitly replacing an undecodable field with a valid edit is allowed.

H7126's public schedule writer reconstructs the settings and writes marker zero.
Keep the existing restrictions: refuse nonzero markers and any unedited mode or
duration the writer cannot preserve. Use the public client methods; do not add
undocumented protocol writes or change the pinned client version.

On write/readback failure, retain pending edits and surface an actionable HA
error. Schedule the existing verification mechanism without replaying controls.
A partial transaction can leave power on or Custom selected; report uncertainty
instead of claiming the schedule was applied. Unload cancels work and cleans up
all radio resources under the existing rules.

## Scope and validation

Update strings and English translations, README capability matrix and usage,
and integration platform setup/unload. No custom dashboard, client protocol
extension, release, hardware operation, or deployment is included.

Offline regressions cover both model encodings; local edits without radio writes;
draft persistence and per-device isolation; fresh schedule merge and preservation;
validation before power/control writes; applying from off and from a non-Custom
mode; Custom readback confirmation; readback mismatch/timeouts/disconnects;
no automatic write replay; edits during Apply; and unload/cleanup.

Run pytest, compileall, Ruff lint/format checks, and the release validator against
the installed pinned client. Hardware round-trip behavior remains unverified
until tested on each physical model; the client explicitly calls for that check.

## Approval

Approved by the user on 2026-10-25. Builder review found nothing important left
to resolve. Product code has not been changed at approval time.

## Follow-up correction and comparison

The user clarified that block 3 is always continuous; no editable final duration
or Continuous toggle is needed. This supersedes the earlier optional finite
final-block design. The existing fan Custom preset still reapplies the saved
schedule; applying the editor enforces the continuous final block.

Reviewed primary examples on 2026-10-25:

- [Official WLED](https://www.home-assistant.io/integrations/wled/#selects):
  each LED segment gets standard select, number, and switch entities. Presets and
  playlists are configured on WLED and activated through HA selects. These are
  spatial segments, not timed fan blocks, and this is not a local draft editor.
- [Official Home Connect](https://www.home-assistant.io/integrations/home_connect/):
  number entities expose program options such as duration; selected and active
  programs are separate, and a start-selected-program action exists. This supports
  separating configuration from execution, but the options belong to the appliance
  rather than a separate persisted HA draft.
- [Custom Govee Life fan implementation](https://github.com/disforw/goveelife/blob/main/custom_components/goveelife/fan.py):
  maps Custom and other modes to fan presets backed by workMode/modeValue commands.
  The reviewed fan platform offers preset selection, not a three-block editor.

Conclusion: standard per-block entity types fit established HA practices.
Persisted local drafts plus one Apply button are specific to this integration's
complete-schedule BLE writer and the user's selected workflow, not a universal
HA custom-program editor convention. No identical built-in three-block fan editor
was found in the examples reviewed. Keep the approved workflow.
