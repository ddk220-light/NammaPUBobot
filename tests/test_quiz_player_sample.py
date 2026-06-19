import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "player_sample",
    pathlib.Path(__file__).resolve().parents[1] / "utils" / "quiz_gen" / "player_sample.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)


def _q(i, metric, answer, closeness=0.8, cat="Villagers"):
    return dict(id=f"player_{i:05d}", category=cat, question_type="top4", grouping="best",
                difficulty="medium", prompt="Who?",
                options=[f"{i}a", f"{i}b", f"{i}c", f"{i}d"],   # distinct option sets
                correct_indices=[0], correct_index=0, multi=False, explanation="x",
                source="player", score=closeness,
                meta=dict(metric_id=metric, answer=answer, closeness=closeness))


def test_take_skips_repeat_metric_and_answer():
    bank = [_q(0, "m1", "alice"), _q(1, "m1", "bob"), _q(2, "m2", "alice"), _q(3, "m3", "carl")]
    take, _ = ps.make_player_taker(bank)
    a = take()
    b = take()
    assert a["meta"]["metric_id"] != b["meta"]["metric_id"]      # no metric repeat
    assert a["meta"]["answer"] != b["meta"]["answer"]            # no answer repeat


def test_take_returns_none_when_exhausted():
    take, _ = ps.make_player_taker([_q(0, "m1", "alice")])
    assert take() is not None
    assert take() is None


def test_theme_filters_to_its_categories():
    bank = [_q(0, "m1", "a", cat="Villagers"), _q(1, "m2", "b", cat="Buildings")]
    take, _ = ps.make_player_taker(bank)
    got = take("Buildings")
    assert got is not None and got["category"] == "Buildings"


def test_metric_and_answer_may_repeat_across_weeks_not_within():
    # same metric/answer is blocked within a week but allowed once the week advances
    bank = [_q(0, "m1", "alice"), _q(1, "m1", "alice")]
    take, _ = ps.make_player_taker(bank)
    assert take(week=1) is not None
    assert take(week=1) is None              # m1/alice already used this week
    assert take(week=2) is not None          # new week -> reusable


def test_relaxed_fallback_used_when_band_excludes_everything():
    # all questions are near-ties (above the strict band) -> only the relaxed pass can pick
    bank = [_q(0, "m1", "a", closeness=0.999), _q(1, "m2", "b", closeness=0.999)]
    take, relaxed = ps.make_player_taker(bank)
    assert take() is not None
    assert relaxed() >= 1
