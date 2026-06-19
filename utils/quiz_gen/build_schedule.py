"""Offline master scheduler: interleave the GAME bank (data/quiz_bank.json) and the
PLAYER bank (data/quiz_bank_player.json) into one ordered, numbered
data/quiz_schedule.json the bot posts one entry per day.

Alternation (per week, resets each week): day 1/3/5/7 -> player, day 2/4/6 -> game,
so the FIRST question of every week is player-based and the two sources alternate.
Keyed on day-within-week (not global seq) so week 2 also starts on player.

    python utils/quiz_gen/build_schedule.py [weeks]      # default 26
"""
import json
import os
import sys

try:
    import player_sample
    import sample_weeks
except ModuleNotFoundError:
    from utils.quiz_gen import player_sample, sample_weeks

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_GAME = os.path.join(_REPO, "data", "quiz_bank.json")
_PLAYER = os.path.join(_REPO, "data", "quiz_bank_player.json")
_BLOCK = os.path.join(_REPO, "data", "quiz_blocklist.json")
_OUT = os.path.join(_REPO, "data", "quiz_schedule.json")
_WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# 3 game slots (even days) and 4 player themes (odd days), rotated by week.
GAME_SLOTS = ["combat", "techgaps", "stats", "combat", "techgaps", "effects"]
PLAYER_THEMES = ["Economy", "Age speed", "Army", "Tech", "Buildings", "Army", "Tech"]


def build(game_bank, player_bank, weeks=26, blocklist=()):
    """Return an ordered, stamped schedule alternating player/game. Pure: takes the two
    banks in memory so it is unit-testable. Empty slots (a source exhausted for that
    category/theme) are skipped, never emitted as None."""
    g_take, _ = sample_weeks.make_game_taker(game_bank, blocklist)
    p_take, _ = player_sample.make_player_taker(player_bank, blocklist)
    out, seq, gi, pi, last_dim = [], 0, 0, 0, None
    for wi in range(1, weeks + 1):
        for day in range(1, 8):
            if day % 2 == 1:                                 # player day
                theme = PLAYER_THEMES[pi % len(PLAYER_THEMES)]
                pi += 1
                q = p_take(theme) or p_take()                # fall back to any theme
                src = "player"
            else:                                            # game day
                cat = GAME_SLOTS[gi % len(GAME_SLOTS)]
                gi += 1
                q = g_take(cat, prefer_fresh_dim=last_dim if cat == "effects" else None)
                if cat == "effects" and q:
                    last_dim = q.get("meta", {}).get("effect")
                src = "game"
            if not q:
                continue
            seq += 1
            out.append({**q, "source": q.get("source", src), "seq": seq,
                        "week": wi, "day": day, "weekday": _WEEKDAY[day - 1]})
    return out


def main():
    weeks = int(sys.argv[1]) if len(sys.argv) > 1 else 26
    with open(_GAME, encoding="utf-8") as f:
        game_bank = json.load(f)
    with open(_PLAYER, encoding="utf-8") as f:
        player_bank = json.load(f)
    block = set()
    if os.path.exists(_BLOCK):
        with open(_BLOCK, encoding="utf-8") as f:
            block = set(json.load(f))
    schedule = build(game_bank, player_bank, weeks, block)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2, ensure_ascii=False)
    n_player = sum(1 for e in schedule if e["source"] == "player")
    n_weeks = max((e["week"] for e in schedule), default=0)
    print(f"Wrote {len(schedule)} questions ({n_weeks} populated weeks of {weeks}) to {_OUT}")
    print(f"  player: {n_player} | game: {len(schedule) - n_player} | blocklisted: {len(block)}")


if __name__ == "__main__":
    main()
