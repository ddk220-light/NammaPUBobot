from bot.replay_stats.apm_query import apm_series
from bot.replay_stats.chart import rolling_mean


ROWS = [
    {"player_number": 1, "minute": 0, "actions": 30},
    {"player_number": 1, "minute": 2, "actions": 50},
    {"player_number": 2, "minute": 0, "actions": 20},
    {"player_number": 2, "minute": 1, "actions": 40},
    {"player_number": 2, "minute": 2, "actions": 60},
]
NAMES = {1: "Alice", 2: "Bob"}

# Extended fixture with an early-eliminated player (player 3) whose max minute (1)
# is strictly less than the match's last minute (3). This ensures the test catches
# regressions where padding is done per-player instead of to the match's last minute.
ROWS_WITH_EARLY_ELIM = [
    {"player_number": 1, "minute": 0, "actions": 30},
    {"player_number": 1, "minute": 2, "actions": 50},
    {"player_number": 2, "minute": 0, "actions": 20},
    {"player_number": 2, "minute": 1, "actions": 40},
    {"player_number": 2, "minute": 2, "actions": 60},
    {"player_number": 3, "minute": 0, "actions": 10},
    {"player_number": 3, "minute": 1, "actions": 25},
    {"player_number": 4, "minute": 0, "actions": 15},
    {"player_number": 4, "minute": 1, "actions": 35},
    {"player_number": 4, "minute": 2, "actions": 45},
    {"player_number": 4, "minute": 3, "actions": 55},
]
NAMES_WITH_EARLY_ELIM = {1: "Alice", 2: "Bob", 3: "Charlie", 4: "Diana"}


def test_series_zero_fills_gaps():
    s = apm_series(ROWS, NAMES)
    alice = next(x for x in s if x["player_number"] == 1)
    # minute 1 is absent from ROWS -> filled with 0 so the line stays continuous
    assert alice["minutes"] == [0, 1, 2]
    assert alice["values"] == [30, 0, 50]


def test_series_pads_all_players_to_the_same_length():
    s = apm_series(ROWS, NAMES)
    assert len({len(x["values"]) for x in s}) == 1


def test_series_peak_and_mean_active():
    s = apm_series(ROWS, NAMES)
    bob = next(x for x in s if x["player_number"] == 2)
    assert bob["peak"] == 60
    assert bob["mean_active"] == 40.0


def test_series_never_exposes_a_bare_mean():
    """`mean_active` divides by the last *active* minute, while replay_players.eapm
    divides by whole game minutes — the two disagree whenever the final action isn't in
    the final minute. A key called plain `mean` invites a consumer to treat it as the
    stored eAPM, which is exactly the parity trap this pipeline exists to avoid."""
    assert all("mean" not in x for x in apm_series(ROWS, NAMES))


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


def test_rolling_mean_clamps_a_non_positive_window():
    # A zero/negative window would slice an empty chunk and divide by zero.
    assert rolling_mean([1, 2, 3], 0) == [1.0, 2.0, 3.0]
    assert rolling_mean([1, 2, 3], -5) == [1.0, 2.0, 3.0]


def test_series_pads_early_eliminated_player_to_match_last_minute():
    """Early-eliminated player (lower max minute) must be zero-filled to match's last minute.

    This catches regressions where padding is per-player instead of to the match's
    global last minute. Player 3 (Charlie) has max minute 1, but match goes to minute 3,
    so Charlie's series should be [10, 25, 0, 0] with minutes [0, 1, 2, 3].
    """
    s = apm_series(ROWS_WITH_EARLY_ELIM, NAMES_WITH_EARLY_ELIM)
    charlie = next(x for x in s if x["player_number"] == 3)

    # Charlie's own data only goes to minute 1, but the match lasts to minute 3
    assert charlie["minutes"] == [0, 1, 2, 3], (
        "Early-eliminated player's minutes should span the full match range"
    )
    assert charlie["values"] == [10, 25, 0, 0], (
        "Early-eliminated player should be zero-filled after their last minute"
    )


def test_series_early_elim_mean_active_reflects_zero_fill():
    """mean_active of an early-eliminated player includes the zero-filled tail."""
    s = apm_series(ROWS_WITH_EARLY_ELIM, NAMES_WITH_EARLY_ELIM)
    charlie = next(x for x in s if x["player_number"] == 3)

    # Charlie's mean_active is (10 + 25 + 0 + 0) / 4 = 8.75
    assert charlie["mean_active"] == 8.75, (
        "mean_active should include zero-filled values from after elimination"
    )


def test_series_early_elim_peak_is_actual_max():
    """Peak of early-eliminated player is their actual max, not affected by padding."""
    s = apm_series(ROWS_WITH_EARLY_ELIM, NAMES_WITH_EARLY_ELIM)
    charlie = next(x for x in s if x["player_number"] == 3)

    # Charlie's peak is the actual max value (25), not affected by zero-padding
    assert charlie["peak"] == 25


def test_series_all_players_zero_filled_to_same_match_last_minute():
    """All players, including early-eliminated ones, padded to the same match length."""
    s = apm_series(ROWS_WITH_EARLY_ELIM, NAMES_WITH_EARLY_ELIM)

    # Match's last minute is 3 (from Diana), so everyone should have 4 values (0..3)
    assert all(len(x["values"]) == 4 for x in s), (
        "All players should be padded to the match's last minute (minute 3)"
    )
    assert all(len(x["minutes"]) == 4 for x in s)
