from bot.replay_stats.render import MIN_MINUTES, should_render


def _series(n_minutes, n_players=2):
    return [{"player_number": p, "name": f"P{p}", "minutes": list(range(n_minutes)),
             "values": [10] * n_minutes, "peak": 10, "mean": 10.0}
            for p in range(1, n_players + 1)]


def test_no_series_is_not_rendered():
    assert should_render([]) is False


def test_too_short_a_match_is_not_rendered():
    # A two-point line is noise, not a chart.
    assert should_render(_series(MIN_MINUTES - 1)) is False


def test_long_enough_match_is_rendered():
    assert should_render(_series(MIN_MINUTES)) is True


def test_single_player_is_still_rendered():
    assert should_render(_series(MIN_MINUTES, n_players=1)) is True
