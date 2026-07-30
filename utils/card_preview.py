#!/usr/bin/env python3
"""Render Match Card text for recent matches, straight from the DB, READ-ONLY.

Usage:  python3 utils/card_preview.py [--limit 5] [--bot-match ID]

Exists so the card can be reviewed against real matches before it posts to
Discord.

Two things this script deliberately does, both mirroring tests/conftest.py:

* It stubs ``core.database.db`` with a wrapper whose ``ensure_table`` is a no-op.
  Importing bot.replay_stats normally runs a dozen ensure_table calls, which
  create tables and add columns — writes. This script must never write, so the
  schema calls are swallowed and only SELECT reaches the server.
* It pre-registers a bare ``bot`` package so bot/__init__.py never runs, which
  keeps nextcord (and the whole Discord client) out of a text-rendering tool.
"""

import argparse
import asyncio
import os
import sys
import types

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from utils.db_helpers import load_config, parse_db_uri  # noqa: E402


class ReadOnlyDB:
    """SELECT-only adapter with the subset of the db interface the card path uses."""

    # bot/replay_stats/__init__.py reads db.types.* while declaring its tables.
    # The values are irrelevant here because ensure_table is a no-op.
    types = types.SimpleNamespace(int="BIGINT", str="VARCHAR(191)", bool="TINYINT(1)",
                                  float="FLOAT", dict="MEDIUMTEXT")

    def __init__(self, conn):
        self._conn = conn

    def ensure_table(self, *_a, **_k):
        # Swallowed on purpose: this is a schema write.
        return None

    async def _query(self, sql, params):
        if not sql.lstrip().upper().startswith("SELECT"):
            raise RuntimeError(f"read-only preview refused a non-SELECT: {sql[:60]}")
        import aiomysql
        cur = await self._conn.cursor(aiomysql.cursors.DictCursor)
        try:
            await cur.execute(sql, params or [])
            return await cur.fetchall()
        finally:
            await cur.close()

    async def fetchall(self, sql, params=None):
        return await self._query(sql, params)

    async def fetchone(self, sql, params=None):
        rows = await self._query(sql, params)
        return rows[0] if rows else None

    select = fetchall
    select_one = fetchone


def _install_stubs(db):
    """Register the fake core.database / bot package modules before any import."""
    fake_db_mod = types.ModuleType("core.database")
    fake_db_mod.db = db
    sys.modules["core.database"] = fake_db_mod

    fake_console = types.ModuleType("core.console")
    fake_console.log = types.SimpleNamespace(
        error=lambda msg: print(f"  [log.error] {msg}", file=sys.stderr),
        info=lambda msg: None, debug=lambda msg: None)
    sys.modules["core.console"] = fake_console

    fake_bot = types.ModuleType("bot")
    fake_bot.__path__ = [os.path.join(PROJECT_ROOT, "bot")]
    sys.modules["bot"] = fake_bot


async def render(pg, db, bot_match_id, meta):
    rows = await pg._analysis_rows(bot_match_id)
    if not rows:
        print(f"\n(bot match {bot_match_id}: no analysis rows)")
        return
    signals = await pg._card_signals_for(rows)
    payloads = [pg._card_payload(row, rows, signals) for row in rows]
    mins = (meta.get("duration_s") or 0) // 60
    print(f"\n{'=' * 74}")
    print(f"bot match {bot_match_id} · {meta.get('map')} · {mins} min")
    print("=" * 74)
    for field in pg._team_card_fields(payloads):
        print(f"\n{field['name']}")
        print(field["value"])
        print(f"[{len(field['value'])} / 1024 chars]")


async def main(limit, bot_match):
    import aiomysql

    cfg = load_config()
    conn = await aiomysql.connect(**parse_db_uri(cfg.DB_URI), autocommit=False)
    try:
        db = ReadOnlyDB(conn)
        _install_stubs(db)
        from bot import post_game as pg

        if bot_match:
            metas = await db.fetchall(
                "SELECT bot_match_id, map, duration_s FROM rs_matches "
                "WHERE bot_match_id=%s", [bot_match])
        else:
            metas = await db.fetchall(
                "SELECT bot_match_id, map, duration_s FROM rs_matches "
                "WHERE bot_match_id IS NOT NULL ORDER BY parsed_at DESC LIMIT %s",
                [limit])
        for meta in metas or []:
            await render(pg, db, meta["bot_match_id"], meta)
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--bot-match", type=int, default=None)
    args = ap.parse_args()
    asyncio.run(main(args.limit, args.bot_match))
