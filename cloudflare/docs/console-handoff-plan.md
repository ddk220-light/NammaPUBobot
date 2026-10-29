# Cloudflare console handoff adoption

Reference: Claude’s design-only commit [`7eec1ae`](https://github.com/ddk220-light/NammaPUBobot/tree/7eec1ae47109e40a88ad7008b147309ab90bbff0/design/cloudflare). Implementation builds on the deployed responsive UI, `8f7d9c2`.

## Scope and order

1. **Backend contracts:** generate minimal, rank-role, and nickname invite URLs from shared permission constants; expose authoritative hidden-player status on organizer records. Keep authorization, public visibility, and storage unchanged.
2. **Guided setup and recovery:** add a resumable, URL-addressable invitation → game → first queue → try Discord guide. Four queue choices and a live illustrative card preview write into the community draft. Save remains a full revision-checked operation. Progress uses saved configuration and verified channel access. Show a permission matrix, feature-specific recovery links, hierarchy instructions, last check, and explicit retry; recheck on returning from Discord without polling full setup.
3. **Queue overview:** show saved queue state, waiting roster/capacity, active recent matches and settings links before the editor. Pause/resume stages a draft; start/clear use the existing reviewed action path and saved rules. Label unsaved queues and saved/live differences.
4. **Players and matches:** expandable player cards place deviation, visibility, profile, rating and restriction tools next to the player, retaining filters and pagination. Split matches into active and recent completed groups, show check-in progress and exact recorded changes, and keep reviewed repair actions beside teams. Do not invent rating or prediction payouts.
5. **Settings guidance:** structured Default / Allowed / Applies to help, duration/team-size readouts, changed-field indicators and a before/after review of the complete community draft. Required identities have no reset. Preserve raw invalid values, units, shared ratings/tax and engine locks.
6. **Verification and delivery:** Worker tests for permission links and private organizer metadata; Chromium/WebKit flows for fresh setup, access failure/return, draft preservation, queue/player actions, match progress, settings review, and responsive reachability. Run existing Cloudflare and converter checks. Capture representative new screens and update PR #46 with the implementation and evidence.

## Guardrails

Keep the dark Community Games design, five organizer destinations, 640/900px breakpoints, 60px phone bar, 44px hit areas, native dialogs and safe-area frame. Public statistics and import safeguards remain in place. No partial imports, permanent restrictions, speculative rating previews, or voice-occupant display. No persistence migration or Railway changes.

The design’s optional game-count panels and background status polling are deferred because the adopted screens do not need them. Delivery recovery uses existing match recovery with current permission diagnostics; generalized outbox retries need their own idempotency/publication design. Real-device file/keyboard/safe-area checks and authenticated Discord acceptance require actual devices/accounts and are reported separately from fixture coverage.

## Implemented

All six implementation stages above are complete for version 0.20.0. The guide uses the same raw draft as the advanced editor, including when a different queue is selected. An abandoned empty custom-game choice cannot leave an unsaveable placeholder behind. Queue actions use saved settings and two-step confirmation; player visibility is read from authoritative organizer metadata. Optional role/nickname access participates in saved readiness. Permission recovery reflects unsaved feature selections without clearing them.

The change review compares effective defaults, avoiding spurious edits when opening older configurations, and identifies its base revision. Rating percentage controls accept the decimals supported by the domain schema and explain zero-scale behavior. All preview copy derives from the draft; no guessed rating deltas or prediction payouts are shown.

Validation: typechecks and web/Worker builds, 882 Worker tests, 100 Chromium/WebKit cases, and 11 converter tests passed locally. The browser suite includes 24 new cases for the guide, permission recovery, queue/player actions, pagination and responsive reachability. Screenshots are emitted for the new screens alongside the existing coverage. A pre-existing backup test was isolated from the live source’s alarm before checking that the restored copy makes no network calls.

This implementation updates PR #46; it is not a new deployment. The existing Cloudflare app remains on 0.19.0 until this revision is published. Physical-device checks, authenticated Discord acceptance, and GitHub’s missing deployment credentials remain open delivery items.
