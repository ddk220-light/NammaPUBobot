"""The quiz calendar (pure arithmetic over a channel's post counter) and the
committed game queue it draws from.

The alternation these tests pin — day 1/3/5/7 player, day 2/4/6 game, player
first, keyed on day-within-week — is the rule stage 5b preserved when the
player half went live. It used to be baked into data/quiz_schedule.json; it is
now stated once in bot/quiz/schedule.source_for_day.
"""
import json
import os

from bot.quiz import schedule as sched
from utils.quiz_gen.sample_weeks import draw


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


# ── the calendar ─────────────────────────────────────────────────────────
def test_first_seq_is_week_one_day_one():
    assert sched.slot_for_seq(1) == (1, 1)


def test_seven_seqs_fill_a_week_then_it_rolls_over():
    assert sched.slot_for_seq(7) == (1, 7)
    assert sched.slot_for_seq(8) == (2, 1)


def test_odd_days_are_player_days_and_even_days_are_game_days():
    assert [sched.source_for_day(d) for d in range(1, 8)] == [
        "player", "game", "player", "game", "player", "game", "player"]


def test_week_two_also_opens_on_a_player_question():
    """7 days/week is odd, so GLOBAL parity flips every week: seq 8 is even.
    Alternation keys on day-within-week precisely so week 2 still opens on
    player — the exact bug the offline scheduler's docstring called out."""
    assert sched.source_for_seq(8) == "player"
    assert sched.source_for_seq(1) == "player"
    assert sched.source_for_seq(2) == "game"


def test_every_week_holds_three_game_days_and_four_player_days():
    for week in (1, 2, 5):
        sources = [sched.source_for_seq(s) for s in sched.seqs_of_week(week)]
        assert sources.count("game") == 3
        assert sources.count("player") == 4


def test_week_is_complete_only_when_all_seven_seqs_are_posted():
    assert sched.week_is_complete(1, set(range(1, 8))) is True
    assert sched.week_is_complete(1, set(range(1, 7))) is False
    assert sched.week_is_complete(2, set(range(1, 15))) is True


def test_completed_weeks_reports_every_finished_week():
    assert sched.completed_weeks(set(range(1, 15))) == [1, 2]
    assert sched.completed_weeks(set(range(1, 10))) == [1]
    assert sched.completed_weeks(set()) == []


def test_completed_weeks_ignores_a_week_with_a_hole_in_it():
    posted = set(range(1, 15)) - {5}
    assert sched.completed_weeks(posted) == [2]


# ── the committed game queue ─────────────────────────────────────────────
_FIX = [
    {"id": "x1", "category": "combat", "prompt": "p", "options": ["a", "b", "c", "d"],
     "correct_indices": [0], "explanation": "e"},
    {"id": "x2", "category": "techgaps", "prompt": "p", "options": ["a", "b", "c", "d"],
     "correct_indices": [1, 2], "explanation": "e"},
]


def test_game_day_takes_the_first_entry_the_channel_has_not_been_asked():
    assert sched.next_game_entry(_FIX, asked_ids=())["id"] == "x1"
    assert sched.next_game_entry(_FIX, asked_ids={"x1"})["id"] == "x2"
    assert sched.next_game_entry(_FIX, asked_ids={"x1", "x2"}) is None


def test_the_committed_schedule_file_loads_and_is_all_game_entries():
    """Game days still read data/quiz_schedule.json, unchanged. Only the player
    half went live."""
    items = sched.load()
    assert items
    assert {q["source"] for q in items} == {"game"}
    assert len({q["id"] for q in items}) == len(items)


def test_loader_drops_a_structurally_incomplete_entry():
    assert sched.load(path=os.devnull) == []
    assert sched.load(path="data/does-not-exist.json") == []
