# Civ Analysis Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make civ_analysis.py faster (parallel API fetching), eliminate manual CSV exports (read from DB directly), improve match accuracy (smarter time scoring), and clean up dead code.

**Architecture:** Rewrite `utils/civ_analysis.py` to use `aiohttp` for concurrent API requests and `aiomysql` for direct DB reads. Extract shared DB connection logic into `utils/db_helpers.py`. Delete `utils/cross_reference_matches.py`. Keep profile map as CSV, keep output CSVs.

**Tech Stack:** Python 3.9+, aiohttp (new dep), aiomysql (existing), asyncio

---

### Task 1: Extract shared DB connection helper

**Files:**
- Create: `utils/db_helpers.py`
- Modify: `utils/analyze_matches.py:142-178`

**Step 1: Create `utils/db_helpers.py`**

Extract the DB URI parsing and pool creation from `analyze_matches.py` into a reusable module:

```python
#!/usr/bin/env python3
"""Shared database connection helpers for utility scripts."""

import os
import sys
from importlib.machinery import SourceFileLoader

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def load_config():
    """Load config.cfg and return the config module."""
    try:
        return SourceFileLoader('cfg', os.path.join(PROJECT_ROOT, 'config.cfg')).load_module()
    except Exception:
        print("Error: Could not load config.cfg. Copy config.example.cfg and fill in DB_URI.",
              file=sys.stderr)
        return None


def parse_db_uri(db_uri):
    """Parse DB_URI string into connection kwargs for aiomysql.create_pool().

    Accepts: mysql://user:password@hostname:port/database
    Returns: dict with host, user, password, db, port keys.
    """
    uri = db_uri
    for prefix in ('mysql://', 'mysql+aiomysql://'):
        if uri.startswith(prefix):
            uri = uri[len(prefix):]
            break

    user, rest = uri.split(':', 1)
    password, rest = rest.split('@', 1)
    host_part, db_name = rest.split('/', 1)
    if ':' in host_part:
        host, port = host_part.split(':')
        port = int(port)
    else:
        host = host_part
        port = 3306

    return dict(host=host, user=user, password=password, db=db_name, port=port)


async def create_pool(db_uri=None):
    """Create and return an aiomysql connection pool.

    If db_uri is None, loads it from config.cfg.
    Returns pool or None on failure.
    """
    import aiomysql

    if db_uri is None:
        cfg = load_config()
        if cfg is None:
            return None
        db_uri = getattr(cfg, 'DB_URI', '')
        if not db_uri:
            print("Error: DB_URI not set in config.cfg", file=sys.stderr)
            return None

    conn_kwargs = parse_db_uri(db_uri)
    return await aiomysql.create_pool(
        **conn_kwargs, charset='utf8mb4', autocommit=True,
        cursorclass=aiomysql.cursors.DictCursor
    )
```

**Step 2: Update `analyze_matches.py` to use the helper**

Replace lines 142-178 of `analyze_matches.py` (the `fetch_db_matches` function's config loading and URI parsing) with:

```python
from utils.db_helpers import create_pool

async def fetch_db_matches(count=10):
    """Fetch the last N matches from the bot's MySQL database."""
    pool = await create_pool()
    if pool is None:
        return None

    async with pool.acquire() as conn:
        # ... rest of function unchanged ...
```

Only replace the config loading + URI parsing + pool creation (lines 144-178). Keep the query logic (lines 180-226) as-is.

**Step 3: Commit**

```bash
git add utils/db_helpers.py utils/analyze_matches.py
git commit -m "refactor: extract shared DB connection helper into utils/db_helpers.py"
```

---

### Task 2: Add aiohttp dependency

**Files:**
- Modify: `requirements.txt`

**Step 1: Add aiohttp to requirements.txt**

Add `aiohttp>=3.9` to `requirements.txt`.

**Step 2: Install**

Run: `pip3 install aiohttp>=3.9`

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add aiohttp for async API fetching"
```

---

### Task 3: Rewrite civ_analysis.py — DB loading + async API fetching

This is the main task. Rewrite `utils/civ_analysis.py` to:
- Load bot matches from MySQL via `db_helpers.py`
- Keep `--csv` flag for CSV fallback mode
- Use `aiohttp` with `asyncio.Semaphore(5)` for parallel API fetching

**Files:**
- Modify: `utils/civ_analysis.py` (full rewrite)

**Step 1: Rewrite the script**

Replace the entire file. Key structural changes:

**Imports** — replace `urllib.request`/`urllib.error` with `aiohttp`, add `asyncio`, add `from utils.db_helpers import create_pool, PROJECT_ROOT`.

**`load_bot_matches_from_db(cutoff)`** — new async function:
```python
async def load_bot_matches_from_db(cutoff):
    """Load bot matches since cutoff directly from MySQL."""
    pool = await create_pool()
    if pool is None:
        return None

    cutoff_ts = int(cutoff.timestamp())

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Get matches with a reported winner since cutoff
            await cur.execute(
                "SELECT match_id, `at`, winner FROM qc_matches "
                "WHERE `at` >= %s AND winner IS NOT NULL "
                "ORDER BY match_id ASC",
                (cutoff_ts,)
            )
            rows = await cur.fetchall()

            match_ids = [r['match_id'] for r in rows]
            if not match_ids:
                pool.close()
                await pool.wait_closed()
                return []

            # Get players for those matches (with nick from qc_players)
            fmt = ','.join(['%s'] * len(match_ids))
            await cur.execute(
                f"SELECT pm.match_id, pm.user_id, pm.team, "
                f"COALESCE(pm.nick, p.nick, pm.user_id) AS nick "
                f"FROM qc_player_matches pm "
                f"LEFT JOIN qc_players p ON pm.user_id = p.user_id AND pm.channel_id = p.channel_id "
                f"WHERE pm.match_id IN ({fmt})",
                match_ids
            )
            player_rows = await cur.fetchall()

    pool.close()
    await pool.wait_closed()

    # Group players by match
    player_map = defaultdict(list)
    for pr in player_rows:
        player_map[pr['match_id']].append({
            'user_id': str(pr['user_id']),
            'nick': pr['nick'],
            'team': int(pr['team']) if pr['team'] is not None else None,
        })

    matches = []
    for r in rows:
        matches.append({
            'match_id': r['match_id'],
            'at': datetime.fromtimestamp(r['at']),
            'winner_team': int(r['winner']),
            'players': player_map.get(r['match_id'], []),
        })
    return matches
```

**`load_bot_matches_from_csv(cutoff)`** — rename existing `load_bot_matches` to this, keep as fallback.

**`async fetch_all_matches_for_player(session, profile_id, cutoff)`** — rewrite to use aiohttp session:
```python
async def fetch_all_matches_for_player(session, semaphore, profile_id, cutoff):
    """Fetch all matches for a player back to cutoff date."""
    all_matches = []
    page = 1
    while True:
        url = f"{AOE2_API}/matches?profile_ids={profile_id}&count=20&page={page}"
        async with semaphore:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        break
                    data = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                break
            await asyncio.sleep(0.2)

        matches = data.get("matches", [])
        if not matches:
            break
        all_matches.extend(matches)

        last_started = matches[-1].get("started", "")
        if last_started:
            last_time = datetime.fromisoformat(
                last_started.replace("Z", "+00:00")
            ).replace(tzinfo=None)
            if last_time < cutoff - timedelta(days=2):
                break

        if len(matches) < 20:
            break
        page += 1
    return all_matches
```

**`async fetch_api_pool(active_pids, pid_to_nick, cutoff)`** — new function that fetches all players concurrently:
```python
async def fetch_api_pool(active_pids, pid_to_nick, cutoff):
    """Fetch API matches for all active players concurrently."""
    semaphore = asyncio.Semaphore(5)
    api_pool = {}

    async with aiohttp.ClientSession(
        headers={"User-Agent": "NammaPUBobot/1.0"}
    ) as session:
        async def fetch_one(pid):
            nick = pid_to_nick.get(pid, str(pid))
            matches = await fetch_all_matches_for_player(session, semaphore, pid, cutoff)
            new = 0
            for m in matches:
                mid = m.get("matchId")
                if mid and mid not in api_pool:
                    api_pool[mid] = m
                    new += 1
            print(f"  {nick} (profile {pid}): {len(matches)} fetched, {new} new")
            return len(matches)

        tasks = [fetch_one(pid) for pid in sorted(active_pids)]
        await asyncio.gather(*tasks)

    return api_pool
```

**`find_aoe2_match_id`** — update scoring to use weighted time + overlap formula:
```python
MAX_TIME_DIFF_MIN = 180

def find_aoe2_match_id(bot_match, nick_to_pids, pid_to_nick, api_pool):
    bot_nicks = {p['nick'] for p in bot_match['players']}

    best_match = None
    best_score = 0

    for api_match in api_pool.values():
        started = api_match.get("started", "")
        if not started:
            continue
        api_time = datetime.fromisoformat(started.replace("Z", "+00:00")).replace(tzinfo=None)

        diff_min = (bot_match['at'] - api_time).total_seconds() / 60
        if not (0 < diff_min < MAX_TIME_DIFF_MIN):
            continue

        api_pids = set()
        for team in api_match.get("teams", []):
            for player in team.get("players", []):
                api_pids.add(player.get("profileId"))

        overlap = 0
        for nick in bot_nicks:
            pids = nick_to_pids.get(nick, [])
            if any(pid in api_pids for pid in pids):
                overlap += 1

        player_score = overlap / len(bot_nicks) if bot_nicks else 0
        time_penalty = diff_min / MAX_TIME_DIFF_MIN
        score = player_score * (1 - 0.3 * time_penalty)

        if score > best_score and score >= 0.4:
            best_score = score
            best_match = api_match

    if best_match:
        return best_match.get("matchId"), best_match
    return None, None
```

**`main()`** — make async, add `--csv` flag:
```python
async def async_main():
    import argparse
    parser = argparse.ArgumentParser(description="Analyze player civ performance from PUB bot matches.")
    parser.add_argument("--days", type=int, default=60, help="Number of days to look back (default: 60)")
    parser.add_argument("--csv", action="store_true", help="Read from CSV exports instead of MySQL")
    args = parser.parse_args()

    cutoff = datetime.now() - timedelta(days=args.days)

    print("Loading data...")
    pid_to_nick, nick_to_pids = load_profile_map()
    cache = load_match_id_map()

    if args.csv:
        bot_matches = load_bot_matches_from_csv(cutoff)
    else:
        bot_matches = await load_bot_matches_from_db(cutoff)
        if bot_matches is None:
            print("Falling back to CSV mode...")
            bot_matches = load_bot_matches_from_csv(cutoff)

    print(f"Found {len(bot_matches)} bot matches in last {args.days} days")
    print(f"Mapped {len(nick_to_pids)} players with profile IDs")
    print(f"Cached match mappings: {len(cache)}")

    # Determine active profile IDs
    active_pids = set()
    for m in bot_matches:
        for p in m['players']:
            for pid in nick_to_pids.get(p['nick'], []):
                active_pids.add(pid)

    # Phase 1: Fetch API matches concurrently
    print(f"\nPhase 1: Fetching API matches for {len(active_pids)} players (concurrent)...")
    api_pool = await fetch_api_pool(active_pids, pid_to_nick, cutoff)
    print(f"Total API matches in pool: {len(api_pool)}")

    # Phase 2 + 3: identical to current (matching + output)
    # ... keep existing Phase 2 + Phase 3 code unchanged ...


def main():
    asyncio.run(async_main())
```

**Step 2: Verify `load_profile_map`, `load_match_id_map`, `save_match_id_map`, `find_player_in_match`** — these functions are unchanged, keep as-is.

**Step 3: Verify Phase 2 and Phase 3 code** — the matching loop and output code are unchanged, keep as-is.

**Step 4: Test manually**

Run with CSV fallback (no DB needed):
```bash
python utils/civ_analysis.py --csv --days 60
```
Expected: Same output as before, but Phase 1 completes faster (concurrent fetching).

Run with DB (if config.cfg available):
```bash
python utils/civ_analysis.py --days 60
```
Expected: Same match count and results. Faster overall.

**Step 5: Commit**

```bash
git add utils/civ_analysis.py
git commit -m "feat: async API fetching + direct DB reads for civ analysis

- Replace urllib with aiohttp for ~5x faster API fetching (Semaphore(5))
- Load bot matches from MySQL directly, skip CSV export step
- Add --csv flag for fallback to old CSV input mode
- Widen match time window (0-180 min) with weighted scoring"
```

---

### Task 4: Delete cross_reference_matches.py

**Files:**
- Delete: `utils/cross_reference_matches.py`

**Step 1: Delete the file**

```bash
git rm utils/cross_reference_matches.py
```

**Step 2: Commit**

```bash
git commit -m "cleanup: remove superseded cross_reference_matches.py"
```

---

### Task 5: End-to-end verification

**Step 1: Run with --csv to baseline**

```bash
python utils/civ_analysis.py --csv --days 60
```

Capture the match count and match rate. Should be identical to before (189 matched, 98%).

**Step 2: Run with DB (if available)**

```bash
python utils/civ_analysis.py --days 60
```

Compare match count — should be same or higher (DB may have newer data than CSV exports).

**Step 3: Compare output CSVs**

Check that `data/match_civ_details.csv` and `data/player_civ_stats.csv` have reasonable data.

**Step 4: Commit updated data files if results changed**

```bash
git add data/match_civ_details.csv data/player_civ_stats.csv data/match_id_map.csv
git commit -m "data: update civ analysis results with improved matching"
```
