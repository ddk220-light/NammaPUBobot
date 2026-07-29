from bot.replay_stats.apm_query import apm_series, rolling_mean


ROWS = [
    {"player_number": 1, "minute": 0, "actions": 30},
    {"player_number": 1, "minute": 2, "actions": 50},
    {"player_number": 2, "minute": 0, "actions": 20},
    {"player_number": 2, "minute": 1, "actions": 40},
    {"player_number": 2, "minute": 2, "actions": 60},
]
NAMES = {1: "Alice", 2: "Bob"}


def test_series_zero_fills_gaps():
    s = apm_series(ROWS, NAMES)
    alice = next(x for x in s if x["player_number"] == 1)
    # minute 1 is absent from ROWS -> filled with 0 so the line stays continuous
    assert alice["minutes"] == [0, 1, 2]
    assert alice["values"] == [30, 0, 50]


def test_series_pads_all_players_to_the_same_length():
    s = apm_series(ROWS, NAMES)
    assert len({len(x["values"]) for x in s}) == 1


def test_series_peak_and_mean():
    s = apm_series(ROWS, NAMES)
    bob = next(x for x in s if x["player_number"] == 2)
    assert bob["peak"] == 60
    assert bob["mean"] == 40.0


def test_series_uses_names_and_falls_back():
    s = apm_series(ROWS, {1: "Alice"})
    assert {x["name"] for x in s} == {"Alice", "Player 2"}


def test_series_empty():
    assert apm_series([], {}) == []


def test_rolling_mean_trailing_window():
    assert rolling_mean([0, 3, 6, 9], 2) == [0.0, 1.5, 4.5, 7.5]


def test_rolling_mean_window_one_is_identity():
    assert rolling_mean([1, 2, 3], 1) == [1.0, 2.0, 3.0]


def test_rolling_mean_empty():
    assert rolling_mean([], 3) == []
