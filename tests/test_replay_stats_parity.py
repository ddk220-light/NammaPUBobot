import os
import sqlite3
import sys

import pytest

REPLAY = os.environ.get("RS_PARITY_REPLAY")
MATCH_ID = os.environ.get("RS_PARITY_MATCH_ID")
QUIZ_DB = os.path.join(os.path.dirname(__file__), "..", "data", "replay_quiz.db")

pytestmark = pytest.mark.skipif(
    not (REPLAY and MATCH_ID and os.path.exists(QUIZ_DB)),
    reason="set RS_PARITY_REPLAY + RS_PARITY_MATCH_ID and have data/replay_quiz.db to run")


def test_live_extract_matches_offline_facts():
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from utils.replay_quiz.extract import extract_match, load_resolved
    out = extract_match(REPLAY, load_resolved(), {})
    assert out["match"]["aoe2_match_id"] == int(MATCH_ID)

    con = sqlite3.connect(QUIZ_DB)
    con.row_factory = sqlite3.Row
    offline = {r["profile_id"]: r for r in con.execute(
        "SELECT profile_id, villagers, feudal_s, military FROM facts WHERE aoe2_match_id=?",
        [int(MATCH_ID)])}
    assert offline, "match not present in offline replay_quiz.db"
    for p in out["players"]:
        o = offline.get(p["profile_id"])
        if not o:
            continue
        assert p["villagers"] == o["villagers"]
        assert p["military"] == o["military"]
        assert (p["feudal_s"] or None) == (o["feudal_s"] or None)
