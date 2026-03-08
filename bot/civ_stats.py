import csv
from pathlib import Path

MIN_GAMES = 3
TOP_N = 5

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "player_civ_stats.csv"

# {nick_lower: [{"civ": str, "wins": int, "losses": int, "games": int, "winrate": float}, ...]}
_civ_data = {}


def load():
    """Load player civ stats from CSV into memory."""
    _civ_data.clear()
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


# Load on import
load()
