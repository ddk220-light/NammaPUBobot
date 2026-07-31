# -*- coding: utf-8 -*-
"""Async DB layer for replay-stats: enable flag, find-next, idempotent per-match write,
and ingest status bookkeeping. All access via core.database.db, except everything
identity — both the profile_id->user_id read and the write-back of what this parse
observed go through the identity resolver (bot/identity.py), which is the single
store answering "who is this person"."""
import time

from bot import identity
from core.config import cfg
from core.console import log
from core.database import db

from . import shape


# ── enable flag ──────────────────────────────────────────────────────────
def is_enabled():
    """Whether this deployment ingests replays at all.

    Deployment configuration, not state: it used to be a single-row ops table
    with one boolean in it, toggled by an admin slash command and read from the
    database on every sweep. 007_raw_renames drops that table in favour of the
    REPLAY_INGEST_ENABLED config var — one switch per deployment, set the same
    way every other deployment-wide switch is (env var on Railway, config.cfg
    locally), and readable without a round trip. Deliberately synchronous for
    the same reason: there is nothing left to await.
    """
    return bool(getattr(cfg, "REPLAY_INGEST_ENABLED", True))


# ── find work ────────────────────────────────────────────────────────────
async def find_new_match(max_age_days=None):
    """Newest replay_match_id (deduped) present in civ_picks but absent from replay_ingest.
    civ_picks has ~8 rows per match, so GROUP BY; join matches for the timestamp.
    Returns dict(replay_match_id, bot_match_id, at) or None."""
    age_clause = ""
    args = []
    if max_age_days is not None:
        age_clause = "AND m.reported_at >= %s "
        args.append(int(time.time()) - max_age_days * 86400)
    rows = await db.fetchall(
        "SELECT mc.replay_match_id AS replay_match_id, MAX(mc.bot_match_id) AS bot_match_id, "
        "MAX(m.reported_at) AS at FROM civ_picks mc JOIN matches m ON m.match_id = mc.bot_match_id "
        "WHERE mc.replay_match_id IS NOT NULL " + age_clause +
        "AND mc.replay_match_id NOT IN (SELECT replay_match_id FROM replay_ingest) "
        "GROUP BY mc.replay_match_id ORDER BY MAX(m.reported_at) DESC LIMIT 1", args)
    return rows[0] if rows else None


async def find_due_retry(now):
    """Oldest ingest row eligible for another attempt (404/parse_failed, due, under cap)."""
    rows = await db.fetchall(
        "SELECT * FROM replay_ingest WHERE status IN ('unavailable','parse_failed') "
        "AND (next_attempt_at IS NULL OR next_attempt_at <= %s) "
        "ORDER BY next_attempt_at ASC LIMIT 1", [now])
    return rows[0] if rows else None


async def reopen_pending_parser_update(current_parser_version):
    """A deploy with a newer parser reopens games shelved on an old parser version."""
    await db.execute(
        "UPDATE replay_ingest SET status='unavailable', next_attempt_at=0 "
        "WHERE status='pending_parser_update' AND (parser_version IS NULL OR parser_version <> %s)",
        [current_parser_version])


async def reset_stale_processing(now):
    """Recover matches orphaned in 'processing' by a crash/redeploy mid-ingest: reset them to
    the retryable 'unavailable' status. Run once per process at first sweep — this process has
    not written any 'processing' row yet, so every existing one is from a dead process."""
    await db.execute(
        "UPDATE replay_ingest SET status='unavailable', next_attempt_at=%s WHERE status='processing'",
        [now])


# ── ingest status ────────────────────────────────────────────────────────
async def get_ingest(replay_match_id):
    return await db.select_one(["*"], "replay_ingest", {"replay_match_id": replay_match_id})


async def upsert_ingest(replay_match_id, **fields):
    cur = await get_ingest(replay_match_id) or dict(replay_match_id=replay_match_id, attempts=0,
                                                    first_seen_at=int(time.time()))
    cur.update(fields)
    await db.insert("replay_ingest", cur, on_duplicate="replace")


# ── per-match write (idempotent) ─────────────────────────────────────────
async def load_profile_user_map(profile_ids):
    """{profile_id: user_id} for every profile_id in `profile_ids` with a known
    Discord owner, via the identity resolver. Profiles with no known owner are
    simply absent — never mapped to None."""
    out = {}
    for pid in profile_ids:
        uid = await identity.user_for_profile(pid)
        if uid is not None:
            out[pid] = uid
    return out


async def _learn_from_ingest(players, profmap):
    """Refresh `identities` with what THIS replay just observed, for every
    profile the resolver already binds to a Discord user.

    What it does now that the legacy replay-side profile table is gone: profmap
    comes from load_profile_user_map, i.e. from identity.user_for_profile, so
    this can never discover a brand-new mapping. It re-asserts a binding the resolver
    already knows, at the same 'learned' tier, which per identity.learn()'s
    "same or lower tier, same user" branch updates exactly two things --
    aoe2_name and last_seen_at. That is the point: `identities.aoe2_name` is
    supposed to be what this account is called IN THE GAME, and this ingest
    holds the freshest possible answer, straight out of the replay
    (extract_match sets `identity` from the parsed player's own name and
    nothing else -- see utils/replay/extract.py's docstring for why a
    Discord nick may never appear here again). last_seen_at then records that
    the profile was genuinely seen playing just now, which is what
    /identity status' coverage window is measured against.

    Best-effort per player: an identity write failing must never break
    ingest -- the raw parse output write_match already wrote is irreplaceable
    (replays 404 upstream once they expire), so each failure is caught and
    logged rather than allowed to propagate.
    """
    for p in players:
        profile_id = p.get("profile_id")
        user_id = profmap.get(profile_id)
        if user_id is None:
            continue
        try:
            await identity.learn(profile_id, user_id, "learned", aoe2_name=p.get("identity") or None)
        except Exception as e:
            log.error(f"Replay-stats identity learn failed for profile_id={profile_id} "
                      f"user_id={user_id}: {e}")


async def write_match(extracted, bot_match_id, parsed_at, parser_version, played_at_epoch=None):
    """Idempotent: replace this match's rows. Returns count of player rows written."""
    aoe2_id = extracted["match"]["aoe2_match_id"]
    profmap = await load_profile_user_map([p["profile_id"] for p in extracted["players"]])
    p2p = shape.pnum_to_profile(extracted["players"])

    # clear any prior rows for this match (idempotent re-ingest)
    for t in ("replay_players", "replay_units", "replay_techs", "replay_buildings",
              "replay_events", "replay_apm"):
        await db.execute(f"DELETE FROM {t} WHERE replay_match_id=%s", [aoe2_id])

    await db.insert("replay_matches",
                    shape.match_row(extracted["match"], bot_match_id, parsed_at, parser_version),
                    on_duplicate="replace")
    # Dual-write the community-owned link table alongside replay_matches.bot_match_id
    # (stage 1.6). replay_matches.bot_match_id keeps being written above exactly as
    # before — this is a deliberate parallel write, not a replacement, until
    # match_replays becomes authoritative in stage 5. A link failure must never
    # break replay ingestion: replays 404 upstream once they expire, so the raw
    # facts just written above are irreplaceable and far more valuable than the
    # link.
    if bot_match_id is not None:
        try:
            from bot.community import link_match_replay
            await link_match_replay(bot_match_id, aoe2_id)
        except Exception as e:
            log.error(f"Replay-stats match_replays link failed for bot_match_id={bot_match_id} "
                      f"(aoe2 match {aoe2_id}): {e}")
    pg = shape.player_game_rows(aoe2_id, extracted["players"], profmap)
    if pg:
        await db.insert_many("replay_players", pg, on_duplicate="replace")
    units = shape.unit_rows(aoe2_id, extracted["units"], p2p)
    if units:
        await db.insert_many("replay_units", units, on_duplicate="replace")
    techs = shape.tech_rows(aoe2_id, extracted["techs"], p2p)
    if techs:
        await db.insert_many("replay_techs", techs, on_duplicate="replace")
    builds = shape.building_rows(aoe2_id, extracted["buildings"], p2p)
    if builds:
        await db.insert_many("replay_buildings", builds, on_duplicate="replace")
    events = shape.event_rows(aoe2_id, extracted.get("events", []), p2p)
    if events:
        await db.insert_many("replay_events", events, on_duplicate="replace")
    apm = shape.apm_rows(aoe2_id, extracted.get("apm", []), p2p)
    if apm:
        await db.insert_many("replay_apm", apm, on_duplicate="replace")
    await _learn_from_ingest(extracted["players"], profmap)
    try:
        from . import classifications
        await classifications.write_extracted_match(extracted, played_at_epoch)
    except Exception as e:
        log.error(f"Replay-stats classification write failed for aoe2 match {aoe2_id}: {e}")
    try:
        from bot.derived import game_stats as _gs
        # played_at_epoch, not parsed_at: the two differ by however long the
        # replay sat in the ingest queue, and player_rollups windows the
        # scouting report on when the game was PLAYED. Passed straight down —
        # the same value replay_matches.played_at was written from above, so a
        # row rebuilt later by bot/derived/backfill.py from that column agrees
        # with this one to the minute.
        await _gs.write(aoe2_id, _gs.compute_game_stats(
            extracted["players"], extracted["units"], extracted.get("apm", []), parsed_at,
            played_at_epoch))
    except Exception as e:
        log.error(f"game_stats write failed ({aoe2_id}): {e}")
    try:
        from . import player_tags
        await player_tags.write_match_tags(aoe2_id)
    except Exception as e:
        log.error(f"Replay-stats player tag write failed for aoe2 match {aoe2_id}: {e}")
    # NOT a persona refresh. Stage 5a retired the generated persona from every
    # surface that read it (`/rank` now renders measured facts out of
    # player_rollups instead), so rs_player_personas stops being written here;
    # migration 009 drops the table in stage 6, which is also when
    # persona.py/persona_store.py go, after that stage verifies no consumer is
    # left. Recomputing a persona nothing reads would only cost every ingest a
    # write and leave a table that looks maintained.
    #
    # A newly paired match is new evidence for the identity deduction solver
    # (bot/identity_solver.py) -- it is what links players in a community with
    # no seed CSVs and no admin willing to curate one. Run it last, after the
    # match_replays link above exists, so this ingest is part of the evidence.
    # run_for_match never raises and skips quietly when the match's channel is
    # not enrolled in a community; the guard here is the same one every other
    # optional post-step in this function carries, because the raw parse
    # already written above is irreplaceable (replays 404 upstream once they
    # expire) and nothing optional may cost us it.
    if bot_match_id is not None:
        try:
            from bot import identity_solver
            await identity_solver.run_for_match(bot_match_id)
        except Exception as e:
            log.error(f"Identity solver run failed for bot match {bot_match_id} "
                      f"(aoe2 match {aoe2_id}): {e}")
    return len(pg)
