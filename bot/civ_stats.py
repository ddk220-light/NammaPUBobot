import csv
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path

MIN_GAMES = 3
TOP_N = 5
MIN_CIV_GAMES = 50

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "player_civ_stats.csv"
_ELO_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "civ_elo_stats.csv"

# {nick_lower: [{"civ": str, "wins": int, "losses": int, "games": int, "winrate": float}, ...]}
_civ_data = {}

_civ_elo_data = {}

IST = timezone(timedelta(hours=5, minutes=30))


def load():
    """Load player civ stats from CSV into memory."""
    _civ_data.clear()
    try:
        with open(_DATA_PATH, newline="") as f:
            for row in csv.DictReader(f):
                nick = row["nick"].lower()
                _civ_data.setdefault(nick, []).append({
                    "civ": row["civ"],
                    "wins": int(row["wins"]),
                    "losses": int(row["losses"]),
                    "games": int(row["games"]),
                    "winrate": float(row["winrate"]),
                })
    except FileNotFoundError:
        print(f"Warning: {_DATA_PATH} not found, civ stats disabled")


def get_player_civs(nick):
    """Return (best, worst, total_qualifying) for a player nick.

    Returns None if player not found.
    best/worst are lists of up to TOP_N civ dicts, sorted by winrate.
    """
    entries = _civ_data.get(nick.lower())
    if not entries:
        return None

    qualified = [e for e in entries if e["games"] >= MIN_GAMES]
    if not qualified:
        return None

    by_wr = sorted(qualified, key=lambda e: (-e["winrate"], -e["games"]))
    total = len(qualified)

    best = by_wr[:TOP_N]
    # Only show worst if there are enough civs that best and worst won't fully overlap
    if total > TOP_N:
        worst = by_wr[-TOP_N:]
        worst.reverse()  # Show lowest winrate first
    else:
        worst = []

    return best, worst, total


def load_civ_elo_stats():
    """Load overall civ winrate stats from CSV into memory."""
    _civ_elo_data.clear()
    try:
        with open(_ELO_DATA_PATH, newline="") as f:
            for row in csv.DictReader(f):
                civ = row["civ"].strip()
                games = int(row["games"])
                if not civ or games < MIN_CIV_GAMES:
                    continue
                _civ_elo_data[civ] = {
                    "civ": civ,
                    "games": games,
                    "winrate": float(row["winrate"]),
                }
    except FileNotFoundError:
        print(f"Warning: {_ELO_DATA_PATH} not found, civ randomization disabled")


def get_all_civs():
    """Return dict of all civs with sufficient data."""
    return dict(_civ_elo_data)


def pick_balanced_teams(excluded_civs=None):
    """Pick 5 civs for each team, balanced by winrate with variety.

    Returns (team_a, team_b) where each is a list of civ dicts sorted by winrate desc.
    Returns None if not enough civ data loaded.
    """
    all_civs = get_all_civs()
    if not all_civs:
        return None

    # Remove excluded civs (case-insensitive match)
    excluded_lower = {c.lower() for c in (excluded_civs or [])}
    available = [c for c in all_civs.values() if c["civ"].lower() not in excluded_lower]

    # If not enough unique civs, allow repeats from the excluded set
    if len(available) < 10:
        available = list(all_civs.values())

    # Sort by winrate to create tiers
    by_wr = sorted(available, key=lambda c: c["winrate"], reverse=True)
    third = len(by_wr) // 3

    top_tier = by_wr[:third]
    mid_tier = by_wr[third:2 * third]
    bot_tier = by_wr[2 * third:]

    # Pick from each tier: 4 top, 2 mid, 4 bottom = 10 total
    def sample(pool, n):
        return random.sample(pool, min(n, len(pool)))

    top_picks = sample(top_tier, 4)
    mid_picks = sample(mid_tier, 2)
    bot_picks = sample(bot_tier, 4)

    all_picks = top_picks + mid_picks + bot_picks

    # If we got fewer than 10 (small pool), pad from remaining available
    if len(all_picks) < 10:
        used = {c["civ"] for c in all_picks}
        remaining = [c for c in available if c["civ"] not in used]
        all_picks += sample(remaining, 10 - len(all_picks))

    # Sort all 10 by winrate descending for snake draft
    all_picks.sort(key=lambda c: c["winrate"], reverse=True)

    # Snake draft: A, B, B, A, A, B, B, A, A, B
    team_a, team_b = [], []
    pattern = [0, 1, 1, 0, 0, 1, 1, 0, 0, 1]  # 0=A, 1=B
    for i, civ in enumerate(all_picks[:10]):
        if pattern[i] == 0:
            team_a.append(civ)
        else:
            team_b.append(civ)

    # Sort each team by winrate desc for display
    team_a.sort(key=lambda c: c["winrate"], reverse=True)
    team_b.sort(key=lambda c: c["winrate"], reverse=True)

    return team_a, team_b


async def get_today_civs(channel):
    """Scan channel history for today's AOE2LobbyBOT match embeds and extract civs played.

    Returns a set of civ name strings.
    """
    now_ist = datetime.now(IST)
    today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    played_civs = set()

    async for msg in channel.history(after=today_start, limit=200):
        # Only look at bot messages with "Match completed" content
        if not msg.author.bot or "Match completed" not in (msg.content or ""):
            continue
        for embed in msg.embeds:
            for field in embed.fields:
                if field.name and field.name.strip() == "Civ":
                    for civ_name in (field.value or "").split("\n"):
                        civ_name = civ_name.strip()
                        if civ_name:
                            played_civs.add(civ_name)

    return played_civs


# Load on import
load()
load_civ_elo_stats()
