# -*- coding: utf-8 -*-
"""Pure decision logic for the replay-stats ingest job: which save versions the pinned
parser handles, and how to back off / give up on retries. No DB, no nextcord, no mgz —
unit-tested in isolation (tests/test_replay_stats_policy.py)."""

# The sanduckhan/aoc-mgz fork (pinned in requirements.txt) parses up to AoE2 DE save 67.x;
# base mgz handles older versions. Anything newer is an un-parseable future patch until the
# fork is bumped (then raise this and bump PARSER_VERSION in __init__.py).
MAX_SUPPORTED_SAVE = 67.99

# Backoff (seconds) for replays not yet on aoe.ms, by prior attempt count.
UNAVAILABLE_BACKOFF = [600, 3600, 21600, 86400]   # 10m, 1h, 6h, 24h
GIVE_UP_UNAVAILABLE_S = 7 * 86400                  # stop retrying a 404 after 7 days
MAX_PARSE_ATTEMPTS = 3                             # corrupt/parse error give-up threshold


def save_version_supported(v):
    """True iff the pinned parser can read this replay's save_version."""
    return v is not None and v <= MAX_SUPPORTED_SAVE


def unavailable_backoff(attempts):
    """Seconds to wait before the next 404 retry, escalating then capping."""
    idx = min(attempts, len(UNAVAILABLE_BACKOFF) - 1)
    return UNAVAILABLE_BACKOFF[idx]


def should_give_up_unavailable(first_seen_at, now):
    """True once a perpetually-404 match has been pending too long."""
    return (now - first_seen_at) >= GIVE_UP_UNAVAILABLE_S


def parse_failed_exhausted(attempts):
    """True once a supported-version replay has failed to parse too many times."""
    return attempts >= MAX_PARSE_ATTEMPTS
