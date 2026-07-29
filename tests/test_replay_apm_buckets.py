from utils.replay_quiz.extract import apm_buckets


def test_buckets_count_actions_per_minute():
    # 3 actions in minute 0, 2 in minute 1, for player 1
    actions = [(1, 0), (1, 30), (1, 59), (1, 60), (1, 119)]
    assert apm_buckets(actions) == [
        {"player_number": 1, "minute": 0, "actions": 3},
        {"player_number": 1, "minute": 1, "actions": 2},
    ]


def test_buckets_separate_players():
    actions = [(1, 10), (2, 10), (2, 20)]
    assert apm_buckets(actions) == [
        {"player_number": 1, "minute": 0, "actions": 1},
        {"player_number": 2, "minute": 0, "actions": 2},
    ]


def test_null_timestamps_are_dropped():
    # Same rule the queue timeline already uses: no timestamp, nowhere to plot it.
    actions = [(1, None), (1, 5)]
    assert apm_buckets(actions) == [{"player_number": 1, "minute": 0, "actions": 1}]


def test_empty_input():
    assert apm_buckets([]) == []


def test_buckets_reconcile_to_mgz_eapm():
    # mgz: total non-AI_ORDER actions / game minutes. 120 actions over 240s -> 30 eapm.
    actions = [(1, t) for t in range(0, 240, 2)]
    buckets = apm_buckets(actions)
    total = sum(b["actions"] for b in buckets)
    assert total == 120
    assert round(total / (240 / 60)) == 30
