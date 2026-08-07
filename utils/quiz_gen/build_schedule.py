"""Offline scheduler for the GAME bank: turn data/quiz_bank.json into the ordered
data/quiz_schedule.json queue the bot draws one entry from on each game day.

WHAT THIS NO LONGER DOES. Until stage 5b this module interleaved two banks --
the game bank and an offline PLAYER bank (data/quiz_bank_player.json, compiled
from parsed replays into a committed SQLite file) -- and stamped every entry
with seq/week/day, so the committed JSON was both the game queue AND the
calendar. The player half is now generated live at post time from
`metric_boards` (nammaoe2bot/features/quiz/player_bank.py), and the calendar is arithmetic the
bot owns (nammaoe2bot/features/quiz/schedule.py's slot_for_seq): a schedule stamped months in
advance cannot say which day a channel is on when that channel started posting
later, was disabled for a fortnight, or had a question forced by an admin. So
this file emits the game queue and nothing else -- no seq, no week, no day.

THE ALTERNATION IS UNCHANGED and is still what shapes this queue: day 1/3/5/7
of each week is a player day, day 2/4/6 a game day (player first, keyed on
day-within-week, so week 2 also opens on a player question). The day loop below
is kept, rather than collapsed to "three picks per week", precisely because the
GAME_SLOTS rotation is indexed by game-day-within-the-schedule: dropping it
would silently re-cut the curated category rotation.

    python utils/quiz_gen/build_schedule.py [weeks]      # default 26
"""
import json
import os
import sys

try:
    import sample_weeks
except ModuleNotFoundError:
    from utils.quiz_gen import sample_weeks

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_GAME = os.path.join(_REPO, "data", "quiz_bank.json")
_BLOCK = os.path.join(_REPO, "data", "quiz_blocklist.json")
_OUT = os.path.join(_REPO, "data", "quiz_schedule.json")

# 3 game slots per week (the even days), rotated across weeks.
GAME_SLOTS = ["combat", "techgaps", "stats", "combat", "techgaps", "effects"]


def build(game_bank, weeks=26, blocklist=()):
    """Return the ordered game queue. Pure: takes the bank in memory so it is
    unit-testable. Empty slots (the source exhausted for that category) are
    skipped, never emitted as None."""
    g_take, _ = sample_weeks.make_game_taker(game_bank, blocklist)
    out, gi, last_dim = [], 0, None
    for _wi in range(1, weeks + 1):
        for day in range(1, 8):
            if day % 2 == 1:
                continue                                 # player day: generated live
            cat = GAME_SLOTS[gi % len(GAME_SLOTS)]
            gi += 1
            q = g_take(cat, prefer_fresh_dim=last_dim if cat == "effects" else None)
            if cat == "effects" and q:
                last_dim = q.get("meta", {}).get("effect")
            if not q:
                continue
            # `source` is the schedule's game/player TAG; a game bank entry's own
            # `source` is a data-provenance string, so set it explicitly rather
            # than inheriting it via {**q}.
            out.append({**q, "source": "game"})
    return out


def main():
    weeks = int(sys.argv[1]) if len(sys.argv) > 1 else 26
    with open(_GAME, encoding="utf-8") as f:
        game_bank = json.load(f)
    block = set()
    if os.path.exists(_BLOCK):
        with open(_BLOCK, encoding="utf-8") as f:
            block = set(json.load(f))
    schedule = build(game_bank, weeks, block)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(schedule)} game questions to {_OUT} "
          f"({weeks} weeks x 3 game days; blocklisted: {len(block)})")


if __name__ == "__main__":
    main()
