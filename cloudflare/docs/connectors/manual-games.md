# Original Generals, Northgard and Tempest Rising

Catalog version: `2026-10-28.1`. Public-source research checked 2026-10-28. Automated contract tests are implemented; actual-game manual acceptance is still pending. Every catalog game retains the existing two-team, captain-confirmed result workflow.

These adapters make no game-server requests. Their manifests explicitly mark account lookup, lobby discovery/observation, match/history lookup and automatic replay references unsupported. They parse and preserve user declarations. No injected hooks, local companion, map exporter, replay download, upload, parsing service or storage is included.

| Game                     | Account declaration                          | Ownership verification                                         | Lobby association                               | Match facts                                                        |
| ------------------------ | -------------------------------------------- | -------------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------ |
| Original Generals        | `network:id`, for example `gameranger:12345` | Unavailable; declaration/organizer assignment remains explicit | Host-supplied opaque lobby or room reference    | Declared faction, team, map and duration; captain-confirmed result |
| Northgard, Steam PC      | SteamID64 or numeric Steam profile URL       | Shared Steam OpenID callback                                   | Host-supplied lobby ID or matching Steam invite | Declared clan, team, map and duration; captain-confirmed result    |
| Tempest Rising, Steam PC | SteamID64 or numeric Steam profile URL       | Shared Steam OpenID callback                                   | Host-supplied opaque lobby reference or invite  | Declared faction, team, map and duration; captain-confirmed result |

Steam authentication proves control of the Steam account. It does not prove ownership of the game, participation in a lobby, the actual faction chosen or the result. Pasting a numeric Steam profile URL is a declaration and never upgrades account trust. Accounts on other stores need a separately declared namespace; these Steam-specific adapters must not guess a cross-platform account mapping.

`generals` keeps its existing catalog ID. `zero_hour` is separate and uses the credentialed GeneralsOnline adapter. An original Generals declaration is not silently relabeled as Zero Hour or GeneralsOnline. Network prefixes distinguish otherwise identical IDs and should describe the network actually used. No claim is made that an arbitrary network exposes stable accounts or ownership verification.

## Using the bot

- `/lobby link profile_id:…` declares an account for the current queue's game, including before a first match. `/profile verify` opens a member-accessible Steam, Battle.net or BAR verification page when supported. Authenticated verification never creates a played match or awards a rating.
- `/lobby attach reference:…` associates the current bot match with a host-supplied room. `/lobby status` shows its association ID, freshness, availability and facts.
- A captain can use `/lobby confirm binding_id:…` after checking the current association and roster. The exact ID prevents a confirmation from applying to a replacement association.
- `/lobby facts selection:…` declares the caller's actual faction/clan. Captains and organizers may also supply `map`, `elapsed_seconds` and an external `recording_url`. Teams come from the bot roster. Declarations do not settle a result.
- `/lobby replays` refreshes provider metadata where supported; manual games remain explicitly unsupported for automatic reference discovery. Existing links and declared facts are displayed in match details.
- Captains continue using `/report loss`, `/report draw` and `/report abort`. Provider or participant facts do not replace those controls.

Organizer Setup shows per-operation availability and switches, missing credentials, acceptance gates, last successful observation and unresolved associations. An unavailable diagnostics request does not prevent settings or manual play. Discord commands must be registered after deploying their updated definitions.

## Inputs and recording references

Lobby references are at most 100 characters and reject control characters, bidi controls, unsafe URI schemes, URL credentials and custom ports. Matching Steam invites are accepted for Northgard app `466560` and Tempest Rising app `1486920`. A plain room name is kept as a declaration; it is never converted into an invented public lobby URL or used for a fetch.

An HTTPS lobby link is a user-provided external link, not provider verification. Discord and web output must escape user text. `externalRecordingReference()` similarly creates `declared-external` metadata with `availability: unknown`; no file is fetched and no retention period applies. A source may remove or restrict a referenced file at any time. Refreshing or validating file contents is outside this build.

Faction/clan declarations are descriptive and must preserve unknown values. Tempest Rising currently includes GDF, Dynasty and Veti; older two-faction assumptions are incomplete. No external clan or faction catalog can establish which one a participant played.

## Tests and acceptance

Run from `cloudflare`:

```sh
npm exec vitest run tests/manual-connectors.test.ts
```

An optional browser check exercises the member verification page, CSRF submission, Discord sign-in return path, organizer capability controls and mobile layout with all API/provider requests mocked. With Playwright Chromium installed, start `npx vite --host 127.0.0.1 --port 4197 --strictPort`, then run `node scripts/connector-ui-smoke.mjs` in another shell. `CONNECTOR_UI_BASE_URL` can select another local server. This is not a live account-verification or actual-game acceptance test.

The deterministic suite checks all registered capability states, namespace separation, lossless Steam identities, strict reference validation and the absence of provider requests in manual mode. The shared workflow suite must additionally cover attachment, unlinking, roster changes, account reassignment and captain reporting for each catalog game.

Before a community adopts a manual connector, play a controlled 1v1 and team game against current game screens. Confirm host lobby reference, participants, actual factions/clans, reported duration and captain settlement. Include a remake, cancellation, unlinked first-time player and changed roster. Record the date/build and outcome in the acceptance ledger. These actions do not require enabling an automatic observation capability.

Recovery is to correct or unlink the mistaken declaration and reattach the current roster. Preserve the audit trail and existing ratings/reporting permissions. A provider outage cannot disable these manual workflows because no provider observation is required.

## Sources

- [Steam website authentication](https://partner.steamgames.com/doc/features/auth#website)
- [Northgard custom lobby ID release notes](https://store.steampowered.com/news/app/466560/view/4101161968277711324)
- [Tempest Rising September 2026 patch notes](https://support.saber.games/hc/en/tempest-rising/articles/september-29-2026-patch-notes-30)
- [GeneralsOnline](https://playgenerals.online/) — explicitly a Zero Hour integration
- [GenTool archive](https://www.gentool.net/data/) — external recording source, not an authenticated match API
