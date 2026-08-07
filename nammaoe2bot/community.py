# -*- coding: utf-8 -*-
"""The community entity — the multi-tenancy root every new table keys on.

A community is one Discord server ("guild" in Discord's API). The inherited
queue/rating tables stay per-channel exactly as they are; this module is the
seed of the unified data layer everything new keys on: every guild that runs
the bot gets exactly one community row, and every channel that belongs to it
is attached to that community.

Two tables declared here via db.ensure_table (NOT a migration — ensure_table
CREATEs any name it does not find at import time, and
tests/test_data_registry.py's scanner needs a declaration here to find them):

  communities         — one row per Discord guild.
  community_channels  — one row per enrolled channel.

`ensure_community`/`attach_channel` (via `enroll_channel`, below) are called
once real Discord guild objects exist — from nammaoe2bot/discord/events.py's on_ready at
boot, and from the two runtime "admin enables the bot on a channel" call
sites (bot/main.py, nammaoe2bot/discord/slash.py). A migration can't do
this enrollment: migrations run before `import bot`, with no Discord
connection at all — see nammaoe2bot/runtime/migrations.py for that ordering.

`enroll_channel` wraps ensure_community()+attach_channel() for a single
channel so all three call sites share one try/except shape instead of
duplicating it — a failure here must never block a channel being enabled or
the bot from booting.

CI installs only pytest (no nextcord/aiomysql/aiohttp), so this module must
import cleanly with nothing but the stdlib and nammaoe2bot.runtime.database/nammaoe2bot.runtime.config
(plus nammaoe2bot.runtime.console, which is equally dependency-free and fully faked by
conftest.py). Do not add a nextcord or nammaoe2bot.runtime.client import at module level;
keep any such import lazy inside a function if one is ever needed here.
"""
import time
import traceback

from nammaoe2bot.runtime.database import db
from nammaoe2bot.runtime.config import cfg
from nammaoe2bot.runtime.console import log

db.ensure_table(dict(
	tname="communities",
	columns=[
		dict(cname="community_id", ctype=db.types.int, autoincrement=True),
		# UNIQUE: Railway rolling deploys can boot two containers at once, so
		# two concurrent ensure_community() calls can both select None for a
		# brand-new guild and both attempt the insert (see ensure_community's
		# IntegrityError handling below). Without a database-level constraint,
		# that race produces two community rows for one Discord server,
		# silently splitting its channels across them. This must land before
		# the table exists in production: _ensure_table only ever ADDs
		# missing columns, it never alters an existing one, so adding this
		# constraint later would need a hand-written migration.
		dict(cname="guild_id", ctype=db.types.int, unique=True),
		dict(cname="name", ctype=db.types.str),
		dict(cname="retention", ctype=db.types.str, notnull=True, default="lean"),
		dict(cname="created_at", ctype=db.types.int),
	],
	primary_keys=["community_id"],
))

db.ensure_table(dict(
	tname="community_channels",
	columns=[
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="added_at", ctype=db.types.int),
	],
	primary_keys=["channel_id"],
))

# match_replays — links a bot match to the replay it was parsed from, keyed
# by community. An AoE2 replay is a fact about a *game*; "our match #1234 is
# that replay" is a fact about a *community*. replay_matches.bot_match_id is a
# single nullable column today, which hardcodes one community owning any
# given replay. This table replaces that assumption for the multi-tenant
# bot: dual-written alongside replay_matches.bot_match_id from stage 1.6 on,
# authoritative from stage 5, and replay_matches.bot_match_id is dropped in
# stage 6. `replay_match_id` is the same column name the raw replay_* tables
# now use, since 007_raw_renames brought them into line with this one.
db.ensure_table(dict(
	tname="match_replays",
	columns=[
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="replay_match_id", ctype=db.types.int),
		# notnull=True because there is no state in which a writer legitimately
		# has nothing to say here: link_match_replay stamps int(time.time()) and
		# 004's backfill falls back through parsed_at -> reported_at -> now, so
		# both writers always supply a value. It was nullable only because
		# nammaoe2bot/runtime/database/mysql.py's column_blank defaults notnull=False, and a
		# NULL would be a third meaning ("linked, at an unknown time") that
		# nammaoe2bot/derived/sweeper.py's retention window has to defend against.
		#
		# This does NOT tighten the live column: _ensure_table only ever ADDs
		# missing columns and never alters nullability, so production's
		# `linked_at` stays nullable until a migration says otherwise. That is
		# exactly why the sweeper keeps its own NULL guard rather than leaning on
		# this declaration -- and why the guard is written to hold per community
		# as well as across them.
		dict(cname="linked_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["community_id", "match_id"],
))

# channel_id -> community_id (or None for a channel resolved-and-unenrolled).
# community_for_channel() is on the hot path (called on every match report),
# hence the cache. invalidate_cache() must be called after every write here.
_channel_cache = {}


async def ensure_community(guild) -> int:
	"""Return the community_id for `guild`, inserting a row if this is the
	first time we've seen it.

	`guild` is a Discord guild object (only `.id` and `.name` are used, so
	any duck-typed stand-in works too). Flagship guilds (listed in
	cfg.FLAGSHIP_GUILD_IDS) always end up at retention 'full' — an existing
	'lean' row is upgraded in place, but 'full' is never downgraded back to
	'lean', even if the guild later drops off the flagship list. This is how
	the current server keeps its full replay history while partner servers
	stay lean.
	"""
	row = await db.select_one(["community_id", "retention"], "communities", where={"guild_id": guild.id})
	is_flagship = guild.id in cfg.FLAGSHIP_GUILD_IDS

	if row is None:
		try:
			community_id = await db.insert("communities", dict(
				guild_id=guild.id,
				name=guild.name,
				retention="full" if is_flagship else "lean",
				created_at=int(time.time()),
			))
			invalidate_cache()
			return community_id
		except db.errors.IntegrityError:
			# Lost a race with another container's concurrent insert for the
			# same guild_id (Railway rolling deploys run two containers at
			# once — this is exactly what the UNIQUE constraint on guild_id
			# above is for). The winner's row now exists; fall through to the
			# existing-row path below so the loser still returns the winner's
			# community_id instead of propagating the error.
			row = await db.select_one(["community_id", "retention"], "communities", where={"guild_id": guild.id})

	community_id = row["community_id"]
	if is_flagship and row["retention"] != "full":
		await db.update("communities", dict(retention="full"), keys=dict(community_id=community_id))
		invalidate_cache()

	return community_id


async def attach_channel(channel_id: int, community_id: int) -> None:
	""" Attach `channel_id` to `community_id`. Idempotent: calling this
	repeatedly for the same channel never errors or duplicates a row. """
	existing = await db.select_one(["community_id"], "community_channels", where={"channel_id": channel_id})
	if existing is not None:
		if existing["community_id"] != community_id:
			await db.update("community_channels", dict(community_id=community_id), keys=dict(channel_id=channel_id))
			invalidate_cache()
		return

	await db.insert("community_channels", dict(
		channel_id=channel_id,
		community_id=community_id,
		added_at=int(time.time()),
	))
	invalidate_cache()


async def enroll_channel(channel) -> int | None:
	""" Enroll `channel` into its guild's community: ensure_community() then
	attach_channel(), as one unit. Returns the community_id, or None if
	enrollment failed. `channel` is a Discord channel object (needs `.id`
	and `.guild`, so any duck-typed stand-in works too).

	Never raises — enrollment is best-effort bookkeeping, not a precondition
	for the channel working, so a DB hiccup here must never stop a channel
	from being enabled or the bot from booting. Callers that care whether
	enrollment actually happened (e.g. on_ready's summary log) can check
	the return value; callers that don't can fire-and-forget.
	"""
	try:
		community_id = await ensure_community(channel.guild)
		await attach_channel(channel.id, community_id)
		return community_id
	except Exception:
		log.error(f"Failed to enroll channel {channel.id} into a community:\n{traceback.format_exc()}")
		return None


async def community_for_channel(channel_id: int) -> int | None:
	""" The community_id `channel_id` is enrolled under, or None if it was
	never enrolled — callers in later stages treat None as "skip". Cached in
	a module-level dict since this runs on every match report.

	Only a successful resolution is cached. If a miss were cached and a
	lookup happened to land between enrollment's write and its
	invalidate_cache() call, the stale None would be cached for the rest of
	the process's lifetime and every later link_match_replay for that
	channel would silently skip forever. A miss is cheap to re-query and
	self-heals on the next call instead. """
	if channel_id in _channel_cache:
		return _channel_cache[channel_id]

	row = await db.select_one(["community_id"], "community_channels", where={"channel_id": channel_id})
	if row is None:
		return None
	community_id = row["community_id"]
	_channel_cache[channel_id] = community_id
	return community_id


async def link_match_replay(match_id: int, replay_match_id: int) -> bool:
	""" Record that `match_id` (a row in `matches`) is `replay_match_id` (an
	AoE2 replay, the same value replay_matches.replay_match_id holds) for
	whichever community owns that match's channel. Returns whether the link
	was written.

	Resolution path: matches.match_id -> matches.channel_id ->
	community_for_channel(channel_id). Both a missing match row and an
	unenrolled channel are expected, benign states (not every match belongs
	to a community yet) — logged at info level, not error, and no row is
	written.

	Idempotent: writes with on_duplicate="replace", so re-linking the same
	(community_id, match_id) pair on a replay re-ingest replaces the row
	instead of raising or duplicating it.
	"""
	match_row = await db.select_one(["channel_id"], "matches", where={"match_id": match_id})
	if match_row is None:
		log.info(f"link_match_replay: no matches row for match_id={match_id}, skipping link")
		return False

	community_id = await community_for_channel(match_row["channel_id"])
	if community_id is None:
		log.info(
			f"link_match_replay: channel {match_row['channel_id']} (match_id={match_id}) "
			"is not enrolled in a community, skipping link"
		)
		return False

	await db.insert("match_replays", dict(
		community_id=community_id,
		match_id=match_id,
		replay_match_id=replay_match_id,
		linked_at=int(time.time()),
	), on_duplicate="replace")
	return True


def invalidate_cache() -> None:
	""" Clear the channel->community cache. Call after every write above. """
	_channel_cache.clear()
