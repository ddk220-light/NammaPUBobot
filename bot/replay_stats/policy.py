# -*- coding: utf-8 -*-
"""Pure decision logic for the replay-stats ingest job: which save versions the pinned
parser handles, and how to back off / give up on retries. No DB, no nextcord, no mgz —
unit-tested in isolation (tests/test_replay_stats_policy.py)."""

# The pinned sanduckhan/aoc-mgz fork parses AoE2 DE replays through save 68.x — VERIFIED
# empirically (2026-06-23: 7/7 real save-68 replays parsed cleanly through extract.py; the
# header format didn't change in a parser-breaking way from 67.2 → 68). Raise this (and bump
# PARSER_VERSION in __init__.py) only after empirically confirming the fork handles a newer
# version, so the pending_parser_update shelf keeps genuinely-unparseable future patches
# recoverable rather than letting them parse_failed.
MAX_SUPPORTED_SAVE = 68.99

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
