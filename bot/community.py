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

`ensure_community`/`attach_channel` are called from bot/events.py's on_ready,
once real Discord guild objects exist. A migration can't do this enrollment:
migrations run before `import bot`, with no Discord connection at all — see
core/migrations.py for that ordering.

CI installs only pytest (no nextcord/aiomysql/aiohttp), so this module must
import cleanly with nothing but the stdlib and core.database/core.config —
both of which conftest.py fakes for the test suite. Do not add a nextcord or
core.client import at module level; keep any such import lazy inside a
function if one is ever needed here.
"""
import time

from core.database import db
from core.config import cfg

db.ensure_table(dict(
	tname="communities",
	columns=[
		dict(cname="community_id", ctype=db.types.int, autoincrement=True),
		dict(cname="guild_id", ctype=db.types.int),
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
		community_id = await db.insert("communities", dict(
			guild_id=guild.id,
			name=guild.name,
			retention="full" if is_flagship else "lean",
			created_at=int(time.time()),
		))
		invalidate_cache()
		return community_id

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


async def community_for_channel(channel_id: int) -> int | None:
	""" The community_id `channel_id` is enrolled under, or None if it was
	never enrolled — callers in later stages treat None as "skip". Cached in
	a module-level dict since this runs on every match report. """
	if channel_id in _channel_cache:
		return _channel_cache[channel_id]

	row = await db.select_one(["community_id"], "community_channels", where={"channel_id": channel_id})
	community_id = row["community_id"] if row else None
	_channel_cache[channel_id] = community_id
	return community_id


async def retention_for(community_id: int) -> str:
	""" 'full' or 'lean' for `community_id`. Defaults to 'lean' (the safe,
	space-conserving choice) if the community_id is unknown. """
	row = await db.select_one(["retention"], "communities", where={"community_id": community_id})
	return row["retention"] if row else "lean"


def invalidate_cache() -> None:
	""" Clear the channel->community cache. Call after every write above. """
	_channel_cache.clear()
