from bot.classifications.query import summarize


def _g(identity, profile_id, winner, archers, fletch):
    return {"identity": identity, "profile_id": profile_id, "winner": winner,
            "archers_pre_castle": archers, "fletching_pre_castle": fletch}


GAMES = [
    _g("Alice", 111, True, 17, 1.0),
    _g("Alice", 111, False, 4, 0.0),
    _g("Bob", 222, True, 12, 1.0),
    _g("Bob", 222, None, 20, 1.0),    # unknown result -> excluded from win rate
]


def test_summarize_counts_and_overall_winrate():
    s = summarize(GAMES)
    assert s["n_games"] == 4
    assert s["n_players"] == 2
    # known-result games: Alice W, Alice L, Bob W -> 2/3
    assert s["overall"] == {"wins": 2, "known": 3, "rate": round(2 / 3, 3)}


def test_summarize_winrate_by_fletching():
    s = summarize(GAMES)
    fl = s["by_fletching"]
    # with fletching: Alice(W), Bob(W), Bob(None->excluded) -> 2/2 ; without: Alice(L) -> 0/1
    assert fl["with"] == {"wins": 2, "known": 2, "rate": 1.0}
    assert fl["without"] == {"wins": 0, "known": 1, "rate": 0.0}


def test_summarize_top_players():
    s = summarize(GAMES)
    top = {p["identity"]: p for p in s["top_players"]}
    assert top["Alice"]["games"] == 2 and top["Bob"]["games"] == 2
    assert top["Alice"]["wins"] == 1 and top["Alice"]["known"] == 2     # rate 0.5


def test_summarize_by_commit_buckets():
    s = summarize(GAMES)
    by_bucket = {b["bucket"]: b for b in s["by_commit"]}
    # Alice(4) -> "4-10"; Alice(17), Bob(12), Bob(20) -> "11-20"
    assert by_bucket["4-10"]["games"] == 1
    assert by_bucket["11-20"]["games"] == 3
    assert "1-3" not in by_bucket and "21+" not in by_bucket   # empty buckets omitted
