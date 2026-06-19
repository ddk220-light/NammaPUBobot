"""Offline: bake data/quiz_bank.json (minus the blocklist) into an ordered, numbered
data/quiz_schedule.json the bot posts one entry per day.

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
_BANK = os.path.join(_REPO, "data", "quiz_bank.json")
_BLOCK = os.path.join(_REPO, "data", "quiz_blocklist.json")
_OUT = os.path.join(_REPO, "data", "quiz_schedule.json")
_WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def stamp(weeks):
    """Flatten weeks->questions into an ordered list, stamping seq/week/day/weekday.
    Skips empty slots (a slot the selector could not fill)."""
    out, seq = [], 0
    for wi, week in enumerate(weeks, 1):
        for di, q in enumerate(week, 1):
            if not q:
                continue
            seq += 1
            out.append({**q, "seq": seq, "week": wi, "day": di, "weekday": _WEEKDAY[di - 1]})
    return out


def main():
    weeks = int(sys.argv[1]) if len(sys.argv) > 1 else 26
    with open(_BANK, encoding="utf-8") as f:
        bank = json.load(f)
    if os.path.exists(_BLOCK):
        with open(_BLOCK, encoding="utf-8") as f:
            block = set(json.load(f))
    else:
        block = set()
    drawn, relaxed = sample_weeks.draw(bank, weeks, block)
    schedule = stamp(drawn)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(schedule)} questions ({weeks} weeks) to {_OUT}")
    print(f"  blocklisted: {len(block)} | relaxed-fallback slots: {relaxed}")
    if relaxed:
        print("  NOTE: freshness facets exhausted -- later weeks reuse opponents/answers.")


if __name__ == "__main__":
    main()
