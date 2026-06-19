import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "sample_weeks",
    pathlib.Path(__file__).resolve().parents[1] / "utils" / "quiz_gen" / "sample_weeks.py")
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)


def _combat(i):
    return dict(id=f"combat_{i:05d}", category="combat", question_type="survive_hp",
                grouping="matchup", difficulty="hard", prompt=f"g{i}?",
                options=[f"A{i}", f"B{i}", f"C{i}", f"D{i}"],
                correct_indices=[0], correct_index=0, multi=False, explanation="x",
                score=0.9, meta={"opp": f"opp{i}", "cluster": "ranged_uu"})


def test_draw_returns_weeks_of_seven_slots():
    bank = [_combat(i) for i in range(60)]
    weeks, _ = sw.draw(bank, 2)
    assert len(weeks) == 2 and all(len(w) == 7 for w in weeks)


def test_make_game_taker_yields_distinct_fresh_questions():
    bank = [_combat(i) for i in range(20)]
    take, relaxed = sw.make_game_taker(bank)
    a = take("combat")
    b = take("combat")
    assert a is not None and b is not None and a["id"] != b["id"]
    assert callable(relaxed) and relaxed() >= 0


def test_make_game_taker_unknown_category_returns_none():
    take, _ = sw.make_game_taker([_combat(0)])
    assert take("nonexistent") is None
