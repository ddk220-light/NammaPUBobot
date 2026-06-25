"""Async DB layer for the OFFLINE runner. Uses a raw aiomysql pool (utils/db_helpers), not the
bot adapter. Idempotent: a re-run of a window overwrites a match's rows for a classification."""
import time

from utils.classifications.schema import CLS_TABLES


async def _exec(pool, sql, args=None):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args or [])


async def ensure_tables(pool):
    for ddl in CLS_TABLES:
        await _exec(pool, ddl)


async def window_matches(pool, days):
    """aoe2_match_id + played_at (epoch) for completed games in the last `days`, newest-first,
    deduped (qc_match_civs has ~8 rows per match). Same source as the live ingest find query."""
    since = int(time.time()) - days * 86400
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT mc.aoe2_match_id AS aoe2_match_id, MAX(m.at) AS played_at "
                "FROM qc_match_civs mc JOIN qc_matches m ON m.match_id = mc.bot_match_id "
                "WHERE mc.aoe2_match_id IS NOT NULL AND m.at >= %s "
                "GROUP BY mc.aoe2_match_id ORDER BY played_at DESC", [since])
            return await cur.fetchall()   # list of dicts (DictCursor)


async def upsert_classification(pool, c):
    """Write the registry row + its data-requirements ledger for one Classification."""
    await _exec(pool,
        "REPLACE INTO cls_classifications (`key`, title, trigger_spec, version, "
        "status, updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
        [c.key, c.title, c.trigger_spec, c.version, c.status, int(time.time())])
    await _exec(pool, "DELETE FROM cls_data_requirements WHERE `key`=%s", [c.key])
    for r in c.requirements:
        await _exec(pool,
            "INSERT INTO cls_data_requirements (`key`, `field`, source, status, note) "
            "VALUES (%s,%s,%s,%s,%s)", [c.key, r["field"], r["source"], r["status"], r["note"]])


async def write_player_totals(pool, totals):
    """Full rebuild of cls_player_totals. totals: {identity: (games, wins, losses)} over EVERY
    scanned player-game (categorized or not) -- the denominator for the web's '% of total games'
    and the source of the 'mixed / uncategorized' remainder."""
    await _exec(pool, "DELETE FROM cls_player_totals")
    for ident, (games, wins, losses) in totals.items():
        await _exec(pool,
            "INSERT INTO cls_player_totals (identity, games, wins, losses) VALUES (%s,%s,%s,%s)",
            [ident, games, wins, losses])


async def wipe_results(pool, key):
    """Delete ALL stored rows for a classification (results + metrics). The runner calls this once
    per classification before a full-window rebuild, so that matches which no longer match (e.g.
    after a trigger change) leave no stale rows behind -- the per-match upsert only deletes matches
    it re-inserts, so a match that drops to zero would otherwise keep its old rows."""
    await _exec(pool, "DELETE FROM cls_results WHERE `key`=%s", [key])
    await _exec(pool, "DELETE FROM cls_result_metrics WHERE `key`=%s", [key])


async def upsert_results(pool, key, aoe2_match_id, result_rows, metric_rows):
    """Replace all rows for (key, aoe2_match_id): delete then insert. Idempotent re-ingest."""
    await _exec(pool, "DELETE FROM cls_results WHERE `key`=%s AND aoe2_match_id=%s",
                [key, aoe2_match_id])
    await _exec(pool, "DELETE FROM cls_result_metrics WHERE `key`=%s AND aoe2_match_id=%s",
                [key, aoe2_match_id])
    for row in result_rows:
        cols = list(row.keys())
        await _exec(pool,
            "INSERT INTO cls_results ({}) VALUES ({})".format(
                ", ".join("`{}`".format(c) for c in cols), ", ".join(["%s"] * len(cols))),
            [row[c] for c in cols])
    for row in metric_rows:
        cols = list(row.keys())
        await _exec(pool,
            "INSERT INTO cls_result_metrics ({}) VALUES ({})".format(
                ", ".join("`{}`".format(c) for c in cols), ", ".join(["%s"] * len(cols))),
            [row[c] for c in cols])
