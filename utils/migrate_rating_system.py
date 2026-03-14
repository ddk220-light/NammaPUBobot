#!/usr/bin/env python3
"""Migrate all existing queue channels to use AoE2 rating system.

Usage:
    python3 utils/migrate_rating_system.py          # dry-run (preview changes)
    python3 utils/migrate_rating_system.py --apply   # actually update the DB
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from db_helpers import create_pool

TARGET_RATING = "AoE2"


async def main():
    apply = "--apply" in sys.argv

    pool = await create_pool()
    if pool is None:
        print("Failed to connect to database.")
        return

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT channel_id, cfg_data FROM qc_configs")
            rows = await cur.fetchall()

    if not rows:
        print("No channels found in qc_configs.")
        pool.close()
        await pool.wait_closed()
        return

    to_update = []
    for row in rows:
        cfg_data = json.loads(row["cfg_data"]) if isinstance(row["cfg_data"], str) else row["cfg_data"]
        current = cfg_data.get("rating_system", "(not set)")
        if current != TARGET_RATING:
            to_update.append((row["channel_id"], current, cfg_data))
        else:
            print(f"  Channel {row['channel_id']}: already {TARGET_RATING}")

    if not to_update:
        print("\nAll channels already use AoE2 rating. Nothing to do.")
        pool.close()
        await pool.wait_closed()
        return

    print(f"\n{len(to_update)} channel(s) to update:")
    for channel_id, current, _ in to_update:
        print(f"  Channel {channel_id}: {current} -> {TARGET_RATING}")

    if not apply:
        print("\nDry run. Pass --apply to update the database.")
        pool.close()
        await pool.wait_closed()
        return

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for channel_id, _, cfg_data in to_update:
                cfg_data["rating_system"] = TARGET_RATING
                await cur.execute(
                    "UPDATE qc_configs SET cfg_data = %s WHERE channel_id = %s",
                    (json.dumps(cfg_data), channel_id),
                )
                print(f"  Updated channel {channel_id}")

    print(f"\nDone. {len(to_update)} channel(s) updated to {TARGET_RATING}.")
    pool.close()
    await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
