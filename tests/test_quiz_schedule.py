import json
import os
from utils.quiz_gen.sample_weeks import draw
from utils.quiz_gen import build_schedule

def _bank():
    with open(os.path.join("data", "quiz_bank.json"), encoding="utf-8") as f:
        return json.load(f)

def test_draw_no_repeated_question_within_run():
    weeks, _ = draw(_bank(), 4)
    sigs = [tuple(sorted(q["options"])) for wk in weeks for q in wk if q]
    assert len(sigs) == len(set(sigs))                 # no option-set ever repeats

def test_draw_respects_blocklist():
    bank = _bank()
    victim = next(q["id"] for q in bank if q["category"] == "stats")
    weeks, _ = draw(bank, 6, blocklist={victim})
    ids = [q["id"] for wk in weeks for q in wk if q]
    assert victim not in ids

def test_build_stamps_sequential_numbers_and_alternates():
    # the unified scheduler stamps seq/week/day inline (replacing the old stamp()).
    game = [{"id": f"combat_{i:05d}", "category": "combat", "options": ["a", "b", "c", "d"],
             "correct_indices": [0], "score": 0.9, "meta": {"opp": f"o{i}"}} for i in range(20)]
    player = [{"id": f"player_{i:05d}", "category": "Villagers", "options": ["w", "x", "y", "z"],
               "correct_indices": [1], "source": "player", "score": 0.8,
               "meta": {"metric_id": f"m{i}", "answer": f"p{i}", "closeness": 0.8}} for i in range(20)]
    flat = build_schedule.build(game, player, weeks=1)
    assert [e["seq"] for e in flat] == list(range(1, len(flat) + 1))
    assert flat[0]["source"] == "player"               # week starts on a player question


from bot.quiz import schedule as sched

_FIX = [
    {"id": "x1", "category": "combat", "seq": 1, "week": 1, "day": 1, "options": ["a", "b", "c", "d"], "correct_indices": [0]},
    {"id": "x2", "category": "techgaps", "seq": 2, "week": 1, "day": 2, "options": ["a", "b", "c", "d"], "correct_indices": [1, 2]},
]

def test_entry_for_seq_returns_match_or_none():
    assert sched.entry_for_seq(_FIX, 2)["id"] == "x2"
    assert sched.entry_for_seq(_FIX, 99) is None

def test_week_is_complete_true_when_all_days_posted():
    # week 1 has days {1,2} in this 2-entry fixture
    assert sched.week_is_complete(_FIX, week=1, posted_seqs={1, 2}) is True
    assert sched.week_is_complete(_FIX, week=1, posted_seqs={1}) is False
