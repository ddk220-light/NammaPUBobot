import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "build_schedule",
    pathlib.Path(__file__).resolve().parents[1] / "utils" / "quiz_gen" / "build_schedule.py")
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)


def _game(i):
    return dict(id=f"combat_{i:05d}", category="combat", question_type="survive_hp",
                grouping="matchup", difficulty="hard", prompt="g?",
                options=["a", "b", "c", "d"], correct_indices=[0], correct_index=0,
                multi=False, explanation="x", score=0.9,
                meta={"opp": f"o{i}", "cluster": "ranged_uu"})


def _player(i):
    return dict(id=f"player_{i:05d}", category="Villagers", question_type="top4",
                grouping="best", difficulty="medium", prompt="p?",
                options=["w", "x", "y", "z"], correct_indices=[1], correct_index=1,
                multi=False, explanation="x", source="player", score=0.8,
                meta={"metric_id": f"m{i}", "answer": f"p{i}", "closeness": 0.8})


def test_week_alternates_player_first():
    game = [_game(i) for i in range(40)]
    player = [_player(i) for i in range(40)]
    sched = bs.build(game, player, weeks=2)
    for e in sched:
        assert e["source"] == ("player" if e["day"] % 2 == 1 else "game")


def test_seq_is_monotonic_one_based():
    sched = bs.build([_game(i) for i in range(40)], [_player(i) for i in range(40)], weeks=2)
    assert [e["seq"] for e in sched] == list(range(1, len(sched) + 1))


def test_every_entry_is_structurally_valid():
    sched = bs.build([_game(i) for i in range(40)], [_player(i) for i in range(40)], weeks=2)
    for e in sched:
        assert len(e["options"]) == 4
        assert 0 <= e["correct_index"] < 4
        assert e["source"] in ("player", "game")
        assert e["weekday"] in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def test_week_two_also_starts_on_player():
    # 7 days/week is odd, so global parity flips each week; alternation must key on
    # day-within-week, not global seq -> week 2 day 1 must still be player.
    sched = bs.build([_game(i) for i in range(40)], [_player(i) for i in range(40)], weeks=2)
    wk2_day1 = next(e for e in sched if e["week"] == 2 and e["day"] == 1)
    assert wk2_day1["source"] == "player"
