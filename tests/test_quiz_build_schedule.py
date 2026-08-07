"""The offline GAME queue builder. Since stage 5b it emits game entries only —
player questions are generated live (nammaoe2bot/features/quiz/player_bank.py) and the calendar
is the bot's arithmetic (nammaoe2bot/features/quiz/schedule.py), so nothing here stamps a
seq/week/day any more. What it still owes: three entries per week (the even
days), in the curated GAME_SLOTS rotation, with no question repeated.
"""
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "build_schedule",
    pathlib.Path(__file__).resolve().parents[1] / "utils" / "quiz_gen" / "build_schedule.py")
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)


def _game(i, category="combat"):
    # Distinct option sets per question: the taker refuses to repeat one, so a
    # fixture that reused ["a","b","c","d"] would emit exactly one entry ever.
    return dict(id=f"{category}_{i:05d}", category=category, question_type="survive_hp",
                grouping="matchup", difficulty="hard", prompt="g?",
                options=[f"{category}{i}{c}" for c in "abcd"], correct_indices=[0],
                correct_index=0, multi=False, explanation="x", score=0.9,
                meta={"opp": f"{category}o{i}", "cluster": "ranged_uu", "effect": f"e{i}"})


def _bank(n=40):
    return [_game(i, c) for c in ("combat", "techgaps", "stats", "effects") for i in range(n)]


def test_every_entry_is_a_game_entry():
    for e in bs.build(_bank(), weeks=4):
        assert e["source"] == "game"


def test_three_entries_per_week_of_game_days():
    # days 2/4/6 are the game days; 4 weeks of a well-stocked bank -> 12 entries.
    assert len(bs.build(_bank(), weeks=4)) == 12


def test_no_seq_week_or_day_is_stamped():
    """The calendar moved into nammaoe2bot/features/quiz/schedule.py. An entry carrying its own
    week/day would be a second opinion about which day a channel is on."""
    for e in bs.build(_bank(), weeks=2):
        assert not {"seq", "week", "day", "weekday"} & set(e)


def test_no_question_is_emitted_twice():
    out = bs.build(_bank(), weeks=8)
    assert len({e["id"] for e in out}) == len(out)


def test_entries_stay_structurally_valid():
    for e in bs.build(_bank(), weeks=4):
        assert len(e["options"]) == 4
        assert 0 <= e["correct_index"] < 4


def test_blocklisted_question_is_never_emitted():
    bank = _bank()
    victim = next(q["id"] for q in bank if q["category"] == "stats")
    out = bs.build(bank, weeks=8, blocklist={victim})
    assert victim not in {e["id"] for e in out}


def test_exhausted_category_is_skipped_not_emitted_as_none():
    out = bs.build([_game(i) for i in range(2)], weeks=4)   # combat only, 2 questions
    assert all(e is not None for e in out)
    assert len(out) == 2
