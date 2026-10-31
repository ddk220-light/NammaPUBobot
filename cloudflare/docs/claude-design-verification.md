# Claude console design verification

Reference: Claude's v2 handoff at commit [`4e6b394`](https://github.com/ddk220-light/NammaPUBobot/tree/4e6b39458b97c6b830e7f358805f423ccbdb5cff), including its desktop and mobile frames. Implementation base: main `4efa411`.

## Visual changes

| Surface        | Applied direction                                                                                                                                                                                                        |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Shared console | Exact semantic light/dark palette, self-hosted Plex 400/600/700 and Mono, 64px desktop top navigation, compact tablet sidebar, 60px phone navigation, restrained borders and radii, account avatar and statistics link   |
| Queues         | Separate list and editor, dense cards with title actions, default/status badges, real roster initials and ratings, capacity bar, saved-policy footer, compact paused state and live-match sidebar                        |
| Queue editor   | Breadcrumb, queue/game/channel context, desktop category rail, touch category strip and search, ruled settings rows, visible dirty fields, readable units and help, persistent community save controls                   |
| Matches        | Compact filters with loaded-record scope, side-by-side teams and aligned ratings, visible check-in state, direct result choice with consequence text and confirmation, delivery/activity sidebar, collapsed repair tools |
| Players        | Table-aligned desktop summaries, labelled phone records, inline management, shortcuts to imports, gold and the selected game's policy; legacy-game opt-in and pagination retained                                        |
| Setup          | Compact verified readiness, named game tiles, shared policy forms, permission recovery and opt-in public publishing; fresh setup uses one focused guide                                                                  |
| Guide          | Desktop step rail, game cards with simple marks, separate first-queue form and illustrative Discord preview, responsive progress, reviewed atomic save                                                                   |
| Change review  | Adds/Creates/Changes/Removes labels, readable old/new values, complete field disclosure, explicit return to editing and save actions                                                                                     |
| Statistics     | Dark community-record header, leaderboard/profile columns on desktop, stacked phone layout, profile sections, recent-rating chart and accessible exact-value table                                                       |
| Public site    | Split hero and illustrative Discord card, game grid, workflow, features, source section, reviewed dashboard screenshots, editorial documentation with contents links and shared footer                                   |

All display records in screenshot fixtures are examples. Live names, ratings, permissions, match outcomes and connector availability come from existing authoritative endpoints.

## Deliberate differences from prototype

- Seven selectable catalog games plus custom; saved legacy games remain usable. The prototype's eleven-game picker is not the agreed product scope.
- Four guide steps represent the saved configuration and verified access. Trying the queue is a manual instruction, not a tracked completion claim.
- Phone/tablet/desktop boundaries remain 640px and 900px, with 44px minimum controls and a 60px phone bar. The reference's smaller targets and inconsistent breakpoint notes are not copied.
- The persistent save bar applies the entire community draft. Live actions still require confirmation and use saved rules; a draft Pause action is visibly staged.
- Ratings are recalculated by the server. The UI does not invent expected rating deltas or promise reversal of paid predictions.
- Delivery repair uses supported match recovery actions. No unsupported retry endpoint is fabricated.
- Public pages retain the current public identity and noindex deployment policy. Search publishing remains explicit and privacy filtering remains authoritative.

These differences make raw whole-page pixel percentages misleading: real content, different data states and required production controls change the pixels. This review does not assert an unmeasured 99% score.

## Verification

`e2e/visual-polish.spec.ts` captures the real UI with labelled demo records in both themes at 390, 768 and 1440px. It also covers result-review invalidation, every profile section, import review, waiting/choice/queue/completed onboarding states, public articles and top-bar hit testing at 901, 1024, 1280 and 1440px.

The existing suite covers 360, 390, 430, 640/641, 768, 844×390, 900/901 and desktop widths; dialog focus and rotation; drafts, network/revision failures; permission recovery; shared ratings; 500-player imports; hidden-player and public publishing policy. Chromium and WebKit run in CI. Screenshot and trace artifacts are uploaded by the Cloudflare workflow.

Settings regression coverage also checks empty map pools, map-option preservation, Gold/predictions dependencies, exact shortlist constraints, malformed section links, help in every category, reverting staged queue state across the list/editor boundary, and preferring current Discord names while labelling cached names with their observation date.

Backend verification includes 1,358 Worker tests, type checking, static HTML/metadata/link validation, Worker bundling and 11 converter tests.

Physical iOS Safari/Android Chrome and a signed-in live Discord round trip require device/user sessions not available in the automated workspace. Local browser fixtures do not count as those checks. Post-deployment public pages, asset hashes, health/source identity, noindex headers and unauthenticated API access are checked separately.
