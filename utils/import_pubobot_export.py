#!/usr/bin/env python3
"""Import a Pubobot CSV export into NammaPUBobot's MySQL database.

Bridges the schema gap between the original Pubobot CSV dump and NammaPUBobot's
extended tables. Handles qc_matches, qc_players, qc_player_matches, and
qc_rating_history. Idempotent — safe to re-run; dedupes by primary key or by
content tuple.

Usage:
    # List channels in qc_configs (read-only)
    python3 utils/import_pubobot_export.py --list-channels

    # Dry-run (read-only) — see counts + sample rows
    python3 utils/import_pubobot_export.py --export-dir export-apr10

    # Apply the import
    python3 utils/import_pubobot_export.py --export-dir export-apr10 --apply

Target channel is auto-detected if exactly one channel exists in qc_configs.
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    print("This script requires Python 3.9+ (zoneinfo).", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, os.path.dirname(__file__))
from db_helpers import create_pool

DEFAULT_ALPHA = "Alpha"
DEFAULT_BETA = "Beta"


# ---------- CSV helpers ----------

def null_or(val, cast=None):
    """Treat CSV 'NULL' / empty string as None; otherwise cast."""
    if val is None or val == "" or val == "NULL":
        return None
    return cast(val) if cast else val


def parse_dt(s, tz):
    """Parse a CSV datetime 'YYYY-MM-DD HH:MM:SS' as naive-in-tz → unix int."""
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return int(dt.replace(tzinfo=tz).timestamp())


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ---------- Channel discovery ----------

async def list_channels(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT channel_id, cfg_data FROM qc_configs ORDER BY channel_id"
            )
            rows = await cur.fetchall()

    if not rows:
        print("No channels found in qc_configs.")
        return

    print(f"{len(rows)} channel(s) in qc_configs:")
    for row in rows:
        cfg = row.get("cfg_data") or "{}"
        if isinstance(cfg, (bytes, bytearray)):
            cfg = cfg.decode("utf-8")
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = {}
        rating_sys = cfg.get("rating_system", "?")
        name_hint = cfg.get("channel_name") or cfg.get("name") or ""
        print(f"  channel_id={row['channel_id']}  rating={rating_sys}  {name_hint}")


async def auto_channel_id(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT channel_id FROM qc_configs")
            rows = await cur.fetchall()

    if not rows:
        raise RuntimeError("No channels exist in qc_configs — nothing to import into.")
    if len(rows) == 1:
        return rows[0]["channel_id"]
    ids = ", ".join(str(r["channel_id"]) for r in rows)
    raise RuntimeError(
        f"{len(rows)} channels exist ({ids}). "
        f"Pass --channel-id to select one, or run --list-channels for details."
    )


# ---------- Row transforms ----------

def transform_match(row, channel_id, tz):
    winner = null_or(row["winner_team"], int)
    if winner == 0:
        alpha_score, beta_score = 1, 0
    elif winner == 1:
        alpha_score, beta_score = 0, 1
    else:
        alpha_score, beta_score = None, None
    return dict(
        match_id=int(row["match_id"]),
        channel_id=channel_id,
        queue_id=None,
        queue_name=row["queue"],
        at=parse_dt(row["at"], tz),
        alpha_name=DEFAULT_ALPHA,
        beta_name=DEFAULT_BETA,
        ranked=1,
        winner=winner,
        alpha_score=alpha_score,
        beta_score=beta_score,
        maps=row.get("maps") or "",
    )


def transform_player(row, channel_id, last_ranked_map):
    user_id = int(row["user_id"])
    return dict(
        channel_id=channel_id,
        user_id=user_id,
        nick=row["nick"],
        is_hidden=int(row["is_hidden"]) if row.get("is_hidden") not in (None, "") else 0,
        rating=null_or(row["rating"], int),
        deviation=null_or(row["deviation"], int),
        wins=int(row.get("wins") or 0),
        losses=int(row.get("losses") or 0),
        draws=int(row.get("draws") or 0),
        streak=int(row.get("streak") or 0),
        last_ranked_match_at=last_ranked_map.get(user_id),
    )


def transform_player_match(row, channel_id, nick_map):
    user_id = int(row["user_id"])
    return dict(
        match_id=int(row["match_id"]),
        channel_id=channel_id,
        user_id=user_id,
        nick=nick_map.get(user_id),
        team=null_or(row["team"], int),
    )


def transform_rating_history(row, channel_id, tz):
    return dict(
        channel_id=channel_id,
        user_id=int(row["user_id"]),
        at=parse_dt(row["at"], tz),
        rating_before=int(row["rating_before"]),
        rating_change=int(row["rating_change"]),
        deviation_before=int(row["deviation_before"]),
        deviation_change=int(row["deviation_change"]),
        match_id=null_or(row["match_id"], int),
        reason=row.get("reason") or None,
    )


# ---------- Existing-state fetchers (read-only) ----------

async def _fetch(pool, sql, params):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def fetch_existing_match_ids(pool, channel_id):
    rows = await _fetch(
        pool,
        "SELECT match_id FROM qc_matches WHERE channel_id = %s",
        (channel_id,),
    )
    return {r["match_id"] for r in rows}


async def fetch_existing_player_matches(pool, channel_id):
    rows = await _fetch(
        pool,
        "SELECT match_id, user_id FROM qc_player_matches WHERE channel_id = %s",
        (channel_id,),
    )
    return {(r["match_id"], r["user_id"]) for r in rows}


async def fetch_existing_player_ids(pool, channel_id):
    rows = await _fetch(
        pool,
        "SELECT user_id FROM qc_players WHERE channel_id = %s",
        (channel_id,),
    )
    return {r["user_id"] for r in rows}


async def fetch_existing_rh_match_pairs(pool, channel_id):
    """Return set of (user_id, match_id) pairs where match_id IS NOT NULL.

    Used to dedupe match-linked rating_history rows: a player can only have one
    rating change per match, so this pair is effectively a natural key.
    """
    rows = await _fetch(
        pool,
        "SELECT user_id, match_id FROM qc_rating_history "
        "WHERE channel_id = %s AND match_id IS NOT NULL",
        (channel_id,),
    )
    return {(r["user_id"], r["match_id"]) for r in rows}


async def fetch_existing_rh_null_tuples(pool, channel_id):
    """Return set of tuples for NULL-match-id rating_history rows (decay/seeding/penalty)."""
    rows = await _fetch(
        pool,
        "SELECT user_id, at, rating_change, reason FROM qc_rating_history "
        "WHERE channel_id = %s AND match_id IS NULL",
        (channel_id,),
    )
    return {
        (r["user_id"], r["at"], r["rating_change"], r["reason"]) for r in rows
    }


async def get_counter(pool):
    rows = await _fetch(pool, "SELECT next_id FROM qc_match_id_counter", ())
    return rows[0]["next_id"] if rows else None


# ---------- Bulk write helpers ----------

async def _bulk_write(pool, table, rows, columns, action):
    """action is 'INSERT IGNORE' | 'INSERT' | 'REPLACE'."""
    if not rows:
        return 0
    col_list = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"{action} INTO `{table}` ({col_list}) VALUES ({placeholders})"
    params = [[r[c] for c in columns] for r in rows]

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(sql, params)
            return cur.rowcount


async def insert_ignore(pool, table, rows, columns):
    return await _bulk_write(pool, table, rows, columns, "INSERT IGNORE")


async def insert_plain(pool, table, rows, columns):
    return await _bulk_write(pool, table, rows, columns, "INSERT")


async def replace_into(pool, table, rows, columns):
    return await _bulk_write(pool, table, rows, columns, "REPLACE")


async def set_counter(pool, value):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT next_id FROM qc_match_id_counter")
            existing = await cur.fetchone()
            if existing is None:
                await cur.execute(
                    "INSERT INTO qc_match_id_counter (next_id) VALUES (%s)", (value,)
                )
            else:
                await cur.execute(
                    "UPDATE qc_match_id_counter SET next_id = %s", (value,)
                )


# ---------- Sample printing ----------

def print_samples(label, rows, limit=3):
    if not rows:
        return
    print(f"\n  Sample {label} (showing {min(limit, len(rows))} of {len(rows)}):")
    for r in rows[:limit]:
        print(f"    {r}")


# ---------- Main import ----------

async def run_import(pool, export_dir, channel_id, tz, apply):
    # --- Read all CSVs ---
    matches_path = os.path.join(export_dir, "qc_matches.csv")
    players_path = os.path.join(export_dir, "qc_players.csv")
    player_matches_path = os.path.join(export_dir, "qc_player_matches.csv")
    rating_history_path = os.path.join(export_dir, "qc_rating_history.csv")

    for p in (matches_path, players_path, player_matches_path, rating_history_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f"Missing CSV: {p}")

    matches_csv = read_csv(matches_path)
    players_csv = read_csv(players_path)
    player_matches_csv = read_csv(player_matches_path)
    rating_history_csv = read_csv(rating_history_path)

    # --- Build lookup maps ---
    nick_map = {int(r["user_id"]): r["nick"] for r in players_csv}

    # For each user, compute max match `at` across their player_matches →
    # populates qc_players.last_ranked_match_at.
    match_at_by_id = {
        int(r["match_id"]): parse_dt(r["at"], tz) for r in matches_csv
    }
    last_ranked_map = {}
    for pm in player_matches_csv:
        uid = int(pm["user_id"])
        at = match_at_by_id.get(int(pm["match_id"]))
        if at is None:
            continue
        prev = last_ranked_map.get(uid)
        if prev is None or at > prev:
            last_ranked_map[uid] = at

    # --- Transform ---
    matches = [transform_match(r, channel_id, tz) for r in matches_csv]
    players = [transform_player(r, channel_id, last_ranked_map) for r in players_csv]
    player_matches = [
        transform_player_match(r, channel_id, nick_map) for r in player_matches_csv
    ]
    rating_history = [
        transform_rating_history(r, channel_id, tz) for r in rating_history_csv
    ]

    # --- Fetch existing state for dedupe ---
    existing_match_ids = await fetch_existing_match_ids(pool, channel_id)
    existing_pm = await fetch_existing_player_matches(pool, channel_id)
    existing_player_ids = await fetch_existing_player_ids(pool, channel_id)
    existing_rh_match_pairs = await fetch_existing_rh_match_pairs(pool, channel_id)
    existing_rh_null = await fetch_existing_rh_null_tuples(pool, channel_id)
    total_existing_rh = len(existing_rh_match_pairs) + len(existing_rh_null)

    # --- Filter new rows ---
    new_matches = [m for m in matches if m["match_id"] not in existing_match_ids]
    new_pm = [
        pm for pm in player_matches
        if (pm["match_id"], pm["user_id"]) not in existing_pm
    ]
    # Rating history: two dedupe strategies depending on match linkage.
    #   - match-linked rows → dedupe by (user_id, match_id). A player can have
    #     at most one rating change per match. Avoids false-new rows caused by
    #     the ~1-second drift between source-bot match time and our elo_sync
    #     recording time.
    #   - NULL-match rows (decay/seeding/penalty) → dedupe by content tuple.
    new_rh_linked = [
        rh for rh in rating_history
        if rh["match_id"] is not None
        and (rh["user_id"], rh["match_id"]) not in existing_rh_match_pairs
    ]
    new_rh_null = [
        rh for rh in rating_history
        if rh["match_id"] is None
        and (rh["user_id"], rh["at"], rh["rating_change"], rh["reason"])
        not in existing_rh_null
    ]
    new_rh = new_rh_linked + new_rh_null
    new_players = [p for p in players if p["user_id"] not in existing_player_ids]
    updated_players = [p for p in players if p["user_id"] in existing_player_ids]

    # --- Summary ---
    print(f"\n=== Import summary (channel_id={channel_id}, tz={tz}) ===")
    print(
        f"qc_matches:         {len(matches_csv):>6} in CSV, "
        f"{len(existing_match_ids):>6} in DB, "
        f"{len(new_matches):>6} new"
    )
    print(
        f"qc_players:         {len(players_csv):>6} in CSV, "
        f"{len(existing_player_ids):>6} in DB, "
        f"{len(new_players):>6} new, "
        f"{len(updated_players):>6} to update"
    )
    print(
        f"qc_player_matches:  {len(player_matches_csv):>6} in CSV, "
        f"{len(existing_pm):>6} in DB, "
        f"{len(new_pm):>6} new"
    )
    print(
        f"qc_rating_history:  {len(rating_history_csv):>6} in CSV, "
        f"{total_existing_rh:>6} in DB, "
        f"{len(new_rh):>6} new  "
        f"(linked={len(new_rh_linked)}, null={len(new_rh_null)})"
    )

    # --- TZ sanity check ---
    if matches_csv:
        csv_at = matches_csv[0]["at"]
        unix_at = parse_dt(csv_at, tz)
        echoed = datetime.fromtimestamp(unix_at, tz=tz).isoformat()
        print(f"\nTZ check: CSV at={csv_at!r} --({tz})--> unix {unix_at}  ({echoed})")

    # --- Samples ---
    print_samples("new qc_matches", new_matches)
    print_samples("new qc_rating_history", new_rh)
    print_samples("new qc_player_matches", new_pm)
    print_samples("new qc_players", new_players)
    print_samples("updated qc_players", updated_players)

    # --- Counter bump preview ---
    max_match_id = max((m["match_id"] for m in matches), default=0)
    current_counter = await get_counter(pool)
    new_counter = max(max_match_id + 1, current_counter or 0)
    print(
        f"\nqc_match_id_counter: current={current_counter}, "
        f"after_import={new_counter}"
    )

    if not apply:
        print("\nDry-run complete. Pass --apply to write.")
        return

    # --- Apply ---
    print("\nApplying...")

    # qc_matches — INSERT IGNORE on PK(match_id)
    match_cols = [
        "match_id", "channel_id", "queue_id", "queue_name", "at",
        "alpha_name", "beta_name", "ranked", "winner",
        "alpha_score", "beta_score", "maps",
    ]
    n = await insert_ignore(pool, "qc_matches", new_matches, match_cols)
    print(f"  qc_matches:        inserted ~{n}")

    # qc_players — REPLACE on PK(user_id, channel_id) so updates flow through
    player_cols = [
        "channel_id", "user_id", "nick", "is_hidden", "rating", "deviation",
        "wins", "losses", "draws", "streak", "last_ranked_match_at",
    ]
    n = await replace_into(pool, "qc_players", players, player_cols)
    print(f"  qc_players:        wrote ~{n} (replace-on-conflict)")

    # qc_player_matches — INSERT IGNORE on PK(match_id, user_id)
    pm_cols = ["match_id", "channel_id", "user_id", "nick", "team"]
    n = await insert_ignore(pool, "qc_player_matches", new_pm, pm_cols)
    print(f"  qc_player_matches: inserted ~{n}")

    # qc_rating_history — pre-filtered, plain INSERT (auto-inc PK)
    rh_cols = [
        "channel_id", "user_id", "at", "rating_before", "rating_change",
        "deviation_before", "deviation_change", "match_id", "reason",
    ]
    n = await insert_plain(pool, "qc_rating_history", new_rh, rh_cols)
    print(f"  qc_rating_history: inserted ~{n}")

    # Counter bump
    if new_counter != (current_counter or 0):
        await set_counter(pool, new_counter)
        print(f"  qc_match_id_counter: set to {new_counter}")

    print("\nDone.")


# ---------- Entry point ----------

async def main():
    ap = argparse.ArgumentParser(
        description="Import a Pubobot CSV export into NammaPUBobot's MySQL DB."
    )
    ap.add_argument("--export-dir", help="Directory with qc_*.csv files")
    ap.add_argument(
        "--channel-id", type=int,
        help="Target channel_id (auto-detected if exactly one exists)"
    )
    ap.add_argument(
        "--timezone", default="UTC",
        help="Timezone to parse CSV datetimes in (default: UTC). "
             "Try America/Los_Angeles if dates look off-by-hours.",
    )
    ap.add_argument(
        "--apply", action="store_true",
        help="Actually write to the DB (default: dry-run)",
    )
    ap.add_argument(
        "--list-channels", action="store_true",
        help="List channels in qc_configs and exit",
    )
    args = ap.parse_args()

    pool = await create_pool()
    if pool is None:
        return 1

    try:
        if args.list_channels:
            await list_channels(pool)
            return 0

        if not args.export_dir:
            print("error: --export-dir is required (unless --list-channels)", file=sys.stderr)
            return 2

        try:
            tz = ZoneInfo(args.timezone)
        except Exception as e:
            print(f"error: invalid --timezone {args.timezone!r}: {e}", file=sys.stderr)
            return 2

        channel_id = args.channel_id
        if channel_id is None:
            channel_id = await auto_channel_id(pool)
            print(f"Auto-detected channel_id={channel_id}")

        await run_import(pool, args.export_dir, channel_id, tz, apply=args.apply)
        return 0

    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
