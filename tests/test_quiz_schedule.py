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

def test_stamp_assigns_sequential_numbers():
    weeks = [[{"id": "a"}, {"id": "b"}], [{"id": "c"}, {"id": "d"}]]
    flat = build_schedule.stamp(weeks)
    assert [e["seq"] for e in flat] == [1, 2, 3, 4]
    assert [e["week"] for e in flat] == [1, 1, 2, 2]
    assert [e["day"] for e in flat] == [1, 2, 1, 2]
