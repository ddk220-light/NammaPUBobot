# Account ownership connectors

Last source and discovery verification: **2026-10-28**. Interactive sign-in acceptance is still required with the deployed bot and registered OAuth clients. Deterministic tests exercise signed assertion validation, callbacks, regional character selection and real Durable Object state consumption with mocked providers.

The bot preserves the difference between a declared profile, an organizer assignment, public profile existence and provider-authenticated ownership. Verification proves control at the recorded time. It does not award match participation, ratings or economy rewards. Duplicate native accounts remain subject to the existing organizer reassignment process; callbacks never reassign another user's account automatically.

## User flow and persistence boundary

1. A Discord-authenticated member submits `POST /auth/game/start` with JSON `{communityId,gameId,provider}` and `X-CSRF-Token`. The request Origin must equal `APP_ORIGIN`. Providers are `steam`, `battlenet` and `bar`.
2. The server verifies current community membership and the configured game, creates a random ten-minute state bound to that Discord user and session, and returns `{url,expiresAt}`. The browser navigates to `url`.
3. The callback must use the same authenticated Discord session. The Durable Object atomically consumes state once, and membership/game compatibility is checked again. Invalid, expired or reused attempts cannot write account ownership.
4. Steam and BAR produce a native provider proof. Battle.net lists only characters returned for the account ID from authenticated `/userinfo`, across US, EU and KR/TW API hosts. The user selects a verified regional character; the selection expires after ten minutes and is single-use.
5. The authoritative Community store applies duplicate-account and reassignment rules. A Steam proof is a Steam64 identity. An Age game's native account is ownership-verified only after a provider returns an unambiguous Steam-to-native-profile crosswalk. A failed or unavailable crosswalk does not upgrade a pasted native profile.

OAuth access tokens and authorization codes are never persisted or logged. Temporary character-choice state contains account IDs and display names, not tokens or match histories. Do not put client secrets, access tokens or signed callback URLs in logs, screenshots or probe artifacts.

## Steam OpenID

No Steam Web API key is needed for OpenID ownership verification. Supported editions here are AoE2 DE, AoE3 DE, AoE4, AoM Retold, Northgard and Tempest Rising. Original Generals does not gain ownership verification merely because a player has Steam.

- Fixed endpoint: `https://steamcommunity.com/openid/login`.
- Callback: `https://<APP_ORIGIN host>/auth/game/callback/steam` (the per-attempt `state` query is included in signed `openid.return_to`).
- The bot checks OpenID 2.0 namespace, exact provider endpoint, claimed identity equality, required signed fields, nonce freshness, duplicate parameters and exact return URL. It posts `check_authentication` to the fixed Steam endpoint before accepting the 17-digit Steam64 string.
- The host must use its configured HTTPS origin. Local testing permits localhost HTTP, matching the bot's existing development origin policy.
- Reference: [Steam OpenID documentation](https://steamcommunity.com/dev).

Recovery: if Steam rejects an assertion, start a new attempt. Never reuse callback queries or treat a profile-page HTTP success as ownership proof.

## Battle.net OAuth

Register a confidential application in the [Battle.net developer portal](https://develop.battle.net/access/clients). Configure exactly:

- Callback: `https://<APP_ORIGIN host>/auth/game/callback/battlenet`
- Server secrets: `BATTLENET_CLIENT_ID`, `BATTLENET_CLIENT_SECRET`
- User scopes: `openid sc2.profile`
- Authorization/token/userinfo: `https://oauth.battle.net/authorize`, `/token`, `/userinfo`

Discovery at `https://oauth.battle.net/.well-known/openid-configuration` returned these endpoints on 2026-10-28. It did not advertise PKCE support. The connector therefore uses the documented confidential authorization-code exchange, the client secret and single-use session-bound state; it does not assume unsupported PKCE behavior. OAuth tokens are used only during the callback to enumerate owned characters.

Public profile/history checks may use a separate `client_credentials` token obtained with `battleNetClientToken`. This grant supplies API access and cannot prove a user's ownership. A service token must never be passed to account-verification persistence as a user proof. Scheduled probes can use configured client credentials or an explicitly supplied short-lived `BATTLE_NET_ACCESS_TOKEN`.

Recovery: 401/403 requires reauthorization or operator credential repair; 429 backs off; missing regional results are shown as unavailable coverage. A missing character in one failed regional request is not a denial of ownership. Selectable characters must come from successful, structurally valid responses for the authenticated account.

Source: [Blizzard OAuth sample](https://github.com/Blizzard/oauth-client-sample/tree/main/java-webmvc), [OAuth guide](https://community.developer.battle.net/documentation/guides/using-oauth).

## Beyond All Reason OAuth

A BAR operator must register the bot's OAuth client before this feature is available. Missing registration is reported as credentials required; declared account linking continues to work.

- Callback: `https://<APP_ORIGIN host>/auth/game/callback/bar`
- Config: `BAR_CLIENT_ID`; `BAR_CLIENT_SECRET` when the registered client is confidential
- Scope: `profile` only
- Fixed endpoints: `https://server4.beyondallreason.info/oauth/authorize`, `/oauth/token`, `/oauth/userinfo`
- PKCE: a random per-attempt verifier and S256 challenge; verifier is discarded after callback consumption
- Stable ID: authenticated `userinfo.sub`, persisted as a string in the `bar:user` namespace. `preferred_username` is display metadata, not identity.

[Live authorization metadata](https://server4.beyondallreason.info/.well-known/oauth-authorization-server) advertised these endpoints, S256 and both public/confidential client authentication on 2026-10-28. Successful discovery does not establish that this bot is registered. See [BAR userinfo implementation](https://github.com/beyond-all-reason/teiserver/blob/main/lib/teiserver_web/controllers/o_auth/userinfo_controller.ex).

Recovery: preserve declared/manual linking while credentials or provider access are unavailable. Re-run interactive acceptance after changing the client registration or redirect origin.

## Tests and release evidence

Run from `cloudflare`:

```sh
npm test -- tests/game-accounts.test.ts tests/sc2-connectors.test.ts
```

Covered: fixed Steam authority and assertion signature checks, expired/duplicate/forged callbacks, same-session ownership, atomic state consumption, CSRF, game changes, failed/revoked OAuth authorization, private credentials never stored in choice state, wide IDs, multi-region selection, unowned-character rejection and authoritative duplicate-link rejection.

Required deployment acceptance remains: Steam sign-in plus Age crosswalk, Battle.net multi-region user selection with actual registered credentials, BAR OAuth with an authorized client, duplicate claims and organizer reassignment across actual community users. These checks must be dated in rollout evidence before labeling ownership sign-in operational in a community.

## Runtime authorization and isolation

Application adapters enforce both deployment-level `CONNECTOR_DISABLED` switches and per-game capability switches before obtaining service tokens or reading a provider. Nested adapter reads are checked at the transport route too: disabling history cannot be bypassed by observation reconciliation. A disabled replay-reference capability also removes provider replay references from combined match/history responses. Controlled-game acceptance gates automatic observation/discovery entry points; a permitted standalone account lookup may still use a provider's public directory route.

The shared provider coordinator keeps only a short-lived **service** bearer in memory. Concurrent grant requests share one pending exchange. Failed grants honor `Retry-After` and back off; revoked credentials, throttling, network unavailability and malformed responses remain distinct. A delayed or missing account/history is local to that request and does not impose a host-wide cooldown. User OAuth tokens are never cached or persisted, and authenticated regional character responses are not cached.

Diagnostics distinguish transport failures from normalized operation failures. `lastSuccessAt` records successful decoding, while `lastTransportSuccessAt` records valid transport JSON. HTTP 200 with an invalid schema cannot reset the observation failure streak. Diagnostics contain counters/times/statuses only, without tokens, profile response bodies or signed URLs.

Additional offline verification:

```sh
npm test -- tests/game-accounts-worker.test.ts tests/connector-runtime-auth.test.ts tests/connector-contract.test.ts
```
