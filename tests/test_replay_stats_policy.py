import bot.replay_stats.policy as p


def test_save_version_supported():
    assert p.save_version_supported(67.2) is True
    assert p.save_version_supported(66.6) is True
    assert p.save_version_supported(68.0) is True    # verified: sanduckhan parses save 68.x
    assert p.save_version_supported(69.0) is False   # a future, not-yet-verified patch
    assert p.save_version_supported(None) is False


def test_unavailable_backoff_escalates_then_caps():
    assert p.unavailable_backoff(0) == 600
    assert p.unavailable_backoff(1) == 3600
    assert p.unavailable_backoff(2) == 21600
    assert p.unavailable_backoff(3) == 86400
    assert p.unavailable_backoff(99) == 86400   # caps at the last step


def test_should_give_up_unavailable_after_7_days():
    now = 1_000_000
    assert p.should_give_up_unavailable(now - 6 * 86400, now) is False
    assert p.should_give_up_unavailable(now - 7 * 86400, now) is True


def test_parse_failed_exhausted_at_3():
    assert p.parse_failed_exhausted(2) is False
    assert p.parse_failed_exhausted(3) is True
