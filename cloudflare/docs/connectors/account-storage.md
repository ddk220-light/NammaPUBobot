# Account trust and compatibility

The community Durable Object remains the account-link writer. Identity callbacks
call `Community.linkVerifiedGameAccount` only after the session-bound OpenID/OAuth
flow; the ordinary `recordAction` schema cannot submit ownership metadata.

Schema 8 adds `profile_claims`, keyed by the existing game/provider/external-ID
key and bound to the Discord owner. It preserves edition, account namespace,
declared/organizer-assigned/ownership-verified trust, verification method/time,
and the authenticated platform ID. The seven-column `profile_refs` table and its
existing keys remain compatible. No identity operation inserts a rating profile,
match participation, rating change, or wallet.

First-time users appear in `/profile show` and statistics with `rated: false`, an
unrated label, zero matches, and their links. They enter the existing rating system
only through its established onboarding workflow. Manual declarations work when a
provider is unsupported, disabled, missing credentials, throttled, or unavailable.
A successful public-profile lookup remains a declaration.

Steam OpenID establishes control of the Steam ID. For an Age-game account, a native
profile becomes ownership-verified only when the registered adapter returns that
exact authenticated Steam ID and one unambiguous native account. Missing, private,
or mismatched mappings leave native declarations unchanged while retaining the
verified Steam link. SC2 keys include region, realm and profile; BAR uses its native
OAuth subject. Provider or community capability switches are checked before and
after asynchronous verification work.

Duplicate native accounts cannot be claimed by a second Discord user through a
callback. Organizers must explicitly reassign them with a reason. Reassignment
clears ownership proof; unlink removes it. Disconnecting or reassigning a Steam
identity also downgrades native claims proved through that identity. Re-saving an
unchanged link for its existing owner preserves an existing proof.

Name/team inference now produces binding suggestions only. Historical `inferred:`
links are displayed as suggestions and cannot establish trusted rosters or played
selections until a player/organizer explicitly confirms them. Played selections
require a verified roster or host/captain-confirmed binding. Captain-confirmed
manual facts contribute faction/clan statistics directly from the final match JSON,
without fabricated provider links; replaced players and test matches are excluded.

Operational backups include `profile_claims`. Restore accepts schemas 4–8 into an
isolated read-only object. Old backups acquire declared/organizer-assigned trust;
they never acquire ownership proof from legacy provenance. A schema-8 backup must
include the new table. Do not deploy a schema-7 binary over schema-8 data; disable a
provider and apply a compatible forward fix.

Legacy generic links also retain an unspecified edition and their original provider
namespace. Only the already-supported AoE2 Companion namespace can be established
from pre-schema-8 records. In particular, an older Mythology/ESO declaration is not
silently relabeled Retold. An unknown or mismatched edition/namespace cannot match
an automated roster or attribute new provider facts; explicit redeclaration through
the current adapter establishes its intended namespace while preserving old history.

Validation:

```sh
npx vitest run tests/account-trust.test.ts tests/staging-baseline.test.ts tests/m10-recovery.test.ts
npx vitest run tests/aoe2-completion.test.ts -t 'identity evidence|civilization attribution'
```

These deterministic tests use SQLite Durable Objects and mocked public providers.
They do not constitute live Steam/Battle.net/BAR callback acceptance or a played-game
acceptance record. SQL trust rows contain no access tokens, refresh tokens, signed
replay URLs, or player histories.
