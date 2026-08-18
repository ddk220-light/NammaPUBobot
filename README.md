# NammaAoe2Bot

A Discord bot for organising Age of Empires II pickup games: queues, rated
matches, replay-derived scouting reports, a play-money gold economy with
betting on live matches, a daily quiz, and a public web dashboard.

It began as a fork of [PUBobot2](https://github.com/Leshaka/PUBobot2) by
**Leshaka** and keeps that project's queue, rating and configuration
foundations. See [Credits](#credits) and [License](#license) — this is GPL-3
software and remains so.

## What it does

- **Pickup queues and rated matches.** Players `++` into a queue, the bot
  balances teams by rating, runs check-in, and records the result. Four rating
  systems (Flat, Glicko2, TrueSkill, AoE2).
- **Replay ingest and scouting reports.** Finished matches are matched to their
  recorded games and parsed, so `/rank` reports what a player actually does:
  opening tendencies, the units they mass, eAPM, and who they beat and lose to
  — windowed to the last 60 days, because a scouting report is a claim about
  how somebody plays now.
- **Gold, betting and a daily quiz.** Pari-mutuel betting on live matches with
  a book that stays open until the AoE2 match actually starts, where match
  participants may back only their own team, and a public 24-hour AoE2 poll
  that pays gold for taking part. Full rules in [docs/GOLD.md](docs/GOLD.md).
- **Civ statistics and balanced random draws**, informed by measured per-civ
  win rates rather than a static list.
- **A public web dashboard** — leaderboards, player pages, civ stats and
  play-style breakdowns — plus an authenticated config surface for admins.
  Public stats are isolated by community and can be public, member-only, or
  administrator-only: `/c/<community_id>/...` selects an explicit community,
  while the unprefixed URLs remain legacy aliases for the configured flagship
  community. Hosted deployments hard-disable replay compute; self-hosted
  operators may enable it per community. Authenticated settings use
  `/api/admin/communities/<community_id>/...`; those routes require Discord
  Manage Guild (or guild/bot ownership), verify every channel through the
  stored community mapping, and require a session CSRF token for mutations.
  Rating onboarding supports manual rows, UTF-8 CSVs, and ratings-only
  Pubobot ZIP previews without overwriting an existing player rating.
  Linked current members can instead be seeded once from their observed AoE2
  ranked-team rating, with external failures blocking partial application.
  A separate guarded migration can atomically import a complete historical
  Pubobot export into an empty channel while remapping legacy match IDs.
  Identity onboarding maps current guild members additively and blocks bulk
  reassignment of a profile already owned by another Discord account.
  The current control scopes and onboarding contract are documented in
  [docs/SETTINGS_MODEL.md](docs/SETTINGS_MODEL.md).

**44 slash commands, 14 of them player-facing.** Every admin group declares
`default_member_permissions`, so Discord hides them from everyone else. See
[COMMANDS.md](COMMANDS.md), and
[the consolidation spec](docs/superpowers/specs/2026-08-06-command-consolidation.md)
for why each of the 57 removed commands went.

## Running it

```bash
pip3 install -r requirements.txt
cp config.example.cfg config.cfg   # fill in Discord + MySQL credentials
python3 -m nammaoe2bot
```

Requires **Python 3.11** and **MySQL**. `start.py` is the deployment wrapper:
it generates `config.cfg` from environment variables and then execs
`python -m nammaoe2bot`. The deployment target is Railway — see
[RAILWAY_SETUP.md](RAILWAY_SETUP.md).

Schema migrations run automatically at boot, before any feature package
declares its tables. Several of them carry post-conditions that will refuse to
start on a database in a state they cannot repair; that is deliberate, and the
crash tells you what to do.

## Layout

```
nammaoe2bot/
  runtime/     config, logging, the Discord client, the DB adapter,
               migrations, paths — everything below the domain
  pickup/      THE DOMAIN: channels, queues, matches, ratings
  features/    built on top and individually optional — betting, quiz,
               lobby, civs, identity, scouting, storylines, postgame
  ingest/      replays in, raw facts out
  derived/     per-game facts and per-player aggregates read from ingest
  discord/     the adapter: slash surface, contexts, event handlers
  web/         the dashboard and the public stats API
  app.py       the one Application — all live state, passed explicitly
  wiring.py    the composition root: the domain announces, features subscribe
  bootstrap.py every import that exists for a side effect, in one place
```

The dependency runs one way, and three static tests keep it that way:
`tests/test_import_cycles.py` (no module-level import cycle),
`tests/test_import_graph.py` (every internal import resolves) and
`tests/test_match_lifecycle.py` (the domain imports no feature). They are
static because nothing in the suite executes the real import graph — a
circular import is a boot crash, not a test failure, and these turn it into
one.

Contributor and architecture notes: [CLAUDE.md](CLAUDE.md).

## Tests

```bash
ruff check .
pytest tests/
```

Both run on every PR via `.github/workflows/ci.yml`.

## Credits

Derived from **[PUBobot2](https://github.com/Leshaka/PUBobot2)** by
**Leshaka** (leshkajm@ya.ru), whose queue, rating, match and configuration
code this project is built on and still contains. If you find this bot useful,
consider supporting the original author on [Boosty](https://boosty.to/leshaka).

Libraries: [nextcord](https://github.com/nextcord/nextcord),
[aiomysql](https://github.com/aio-libs/aiomysql),
[mgz](https://github.com/happyleavesaoc/aoc-mgz),
[glicko2](https://github.com/deepy/glicko2),
[TrueSkill](https://trueskill.org/).

## License

Copyright (C) 2020 **Leshaka**, and contributors to this fork.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License version 3 as published by the Free
Software Foundation.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

See [LICENSE](LICENSE) for the full GNU General Public License v3.
