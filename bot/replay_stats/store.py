# -*- coding: utf-8 -*-
"""Async DB layer for replay-stats: enable flag, find-next, idempotent per-match write,
ingest status bookkeeping, and rs_profiles seeding/lookup. All access via core.database.db."""
import csv
import os
import time

from core.console import log
from core.database import db

from . import shape

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ── enable flag ──────────────────────────────────────────────────────────
async def is_enabled():
    row = await db.select_one(["*"], "rs_config", {"id": 1})
    return bool(row and row.get("enabled"))


async def set_enabled(on):
    await db.insert("rs_config", dict(id=1, enabled=1 if on else 0), on_dublicate="replace")


# ── find work ────────────────────────────────────────────────────────────
async def find_new_match(max_age_days=None):
    """Newest aoe2_match_id (deduped) present in qc_match_civs but absent from rs_ingest.
    qc_match_civs has ~8 rows per match, so GROUP BY; join qc_matches for the timestamp.
    Returns dict(aoe2_match_id, bot_match_id, at) or None."""
    age_clause = ""
    args = []
    if max_age_days is not None:
        age_clause = "AND m.at >= %s "
        args.append(int(time.time()) - max_age_days * 86400)
    rows = await db.fetchall(
        "SELECT mc.aoe2_match_id AS aoe2_match_id, MAX(mc.bot_match_id) AS bot_match_id, "
        "MAX(m.at) AS at FROM qc_match_civs mc JOIN qc_matches m ON m.match_id = mc.bot_match_id "
        "WHERE mc.aoe2_match_id IS NOT NULL " + age_clause +
        "AND mc.aoe2_match_id NOT IN (SELECT aoe2_match_id FROM rs_ingest) "
        "GROUP BY mc.aoe2_match_id ORDER BY at DESC LIMIT 1", args)
    return rows[0] if rows else None


async def find_due_retry(now):
    """Oldest ingest row eligible for another attempt (404/parse_failed, due, under cap)."""
    rows = await db.fetchall(
        "SELECT * FROM rs_ingest WHERE status IN ('unavailable','parse_failed') "
        "AND (next_attempt_at IS NULL OR next_attempt_at <= %s) "
        "ORDER BY next_attempt_at ASC LIMIT 1", [now])
    return rows[0] if rows else None


async def reopen_pending_parser_update(current_parser_version):
    """A deploy with a newer parser reopens games shelved on an old parser version."""
    await db.execute(
        "UPDATE rs_ingest SET status='unavailable', next_attempt_at=0 "
        "WHERE status='pending_parser_update' AND (parser_version IS NULL OR parser_version <> %s)",
        [current_parser_version])


async def reset_stale_processing(now):
    """Recover matches orphaned in 'processing' by a crash/redeploy mid-ingest: reset them to
    the retryable 'unavailable' status. Run once per process at first sweep — this process has
    not written any 'processing' row yet, so every existing one is from a dead process."""
    await db.execute(
        "UPDATE rs_ingest SET status='unavailable', next_attempt_at=%s WHERE status='processing'",
        [now])


# ── ingest status ────────────────────────────────────────────────────────
async def get_ingest(aoe2_match_id):
    return await db.select_one(["*"], "rs_ingest", {"aoe2_match_id": aoe2_match_id})


async def upsert_ingest(aoe2_match_id, **fields):
    cur = await get_ingest(aoe2_match_id) or dict(aoe2_match_id=aoe2_match_id, attempts=0,
                                                  first_seen_at=int(time.time()))
    cur.update(fields)
    await db.insert("rs_ingest", cur, on_dublicate="replace")


# ── per-match write (idempotent) ─────────────────────────────────────────
async def load_profile_user_map():
    rows = await db.fetchall("SELECT profile_id, user_id FROM rs_profiles WHERE user_id IS NOT NULL")
    return {r["profile_id"]: r["user_id"] for r in rows}


async def write_match(extracted, bot_match_id, parsed_at, parser_version, played_at_epoch=None):
    """Idempotent: replace this match's rows. Returns count of player rows written."""
    aoe2_id = extracted["match"]["aoe2_match_id"]
    profmap = await load_profile_user_map()
    p2p = shape.pnum_to_profile(extracted["players"])

    # clear any prior rows for this match (idempotent re-ingest)
    for t in ("rs_player_games", "rs_player_units", "rs_player_techs", "rs_player_buildings",
              "rs_player_events"):
        await db.execute(f"DELETE FROM {t} WHERE aoe2_match_id=%s", [aoe2_id])

    await db.insert("rs_matches",
                    shape.match_row(extracted["match"], bot_match_id, parsed_at, parser_version),
                    on_dublicate="replace")
    pg = shape.player_game_rows(aoe2_id, extracted["players"], profmap)
    if pg:
        await db.insert_many("rs_player_games", pg, on_dublicate="replace")
    units = shape.unit_rows(aoe2_id, extracted["units"], p2p)
    if units:
        await db.insert_many("rs_player_units", units, on_dublicate="replace")
    techs = shape.tech_rows(aoe2_id, extracted["techs"], p2p)
    if techs:
        await db.insert_many("rs_player_techs", techs, on_dublicate="replace")
    builds = shape.building_rows(aoe2_id, extracted["buildings"], p2p)
    if builds:
        await db.insert_many("rs_player_buildings", builds, on_dublicate="replace")
    events = shape.event_rows(aoe2_id, extracted.get("events", []), p2p)
    if events:
        await db.insert_many("rs_player_events", events, on_dublicate="replace")
    profs = shape.profile_upserts(extracted["players"], profmap, parsed_at)
    if profs:
        await db.insert_many("rs_profiles", profs, on_dublicate="replace")
    try:
        from . import classifications
        await classifications.write_extracted_match(extracted, played_at_epoch)
    except Exception as e:
        log.error(f"Replay-stats classification write failed for aoe2 match {aoe2_id}: {e}")
    try:
        from . import player_tags
        await player_tags.write_match_tags(aoe2_id)
    except Exception as e:
        log.error(f"Replay-stats player tag write failed for aoe2 match {aoe2_id}: {e}")
    try:
        from . import persona_store
        await persona_store.refresh_match_users(aoe2_id)
    except Exception as e:
        log.error(f"Replay-stats persona refresh failed for aoe2 match {aoe2_id}: {e}")
    return len(pg)


# ── profile seeding (one-time / idempotent) ──────────────────────────────
async def seed_profiles_from_csv():
    """Seed rs_profiles from data/profile_resolved.csv (cols: profile_id,user_id,nick,...).
    Only inserts profiles not already present (preserves learned user_ids)."""
    path = os.path.join(_ROOT, "data", "profile_resolved.csv")
    if not os.path.exists(path):
        return 0
    existing = {r["profile_id"] for r in await db.fetchall("SELECT profile_id FROM rs_profiles")}
    rows, now = [], int(time.time())
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                pid = int(r["profile_id"])
            except (ValueError, KeyError):
                continue
            if pid in existing:
                continue
            uid = r.get("user_id")
            rows.append(dict(profile_id=pid, user_id=int(uid) if uid else None,
                             name=r.get("nick") or r.get("aoe2_name") or "", last_seen_at=now))
    if rows:
        await db.insert_many("rs_profiles", rows, on_dublicate="ignore")
    return len(rows)
