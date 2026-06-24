from bot.classifications.query import roster, winners_vs_losers


def _r(identity, pid, winner, metrics):
    return {"identity": identity, "profile_id": pid, "winner": winner, "metrics": metrics}


RESULTS = [
    _r("Alice", 111, True, {"archers_pre_castle": 17.0, "fletching_pre_castle": 1.0, "castle_s": 1400.0}),
    _r("Alice", 111, False, {"archers_pre_castle": 4.0, "fletching_pre_castle": 0.0, "castle_s": 1300.0}),
    _r("Bob", 222, True, {"archers_pre_castle": 12.0, "fletching_pre_castle": 1.0, "castle_s": 1500.0}),
    _r("Bob", 222, None, {"archers_pre_castle": 20.0, "fletching_pre_castle": 1.0, "castle_s": 1600.0}),
]

SPECS = [
    {"metric": "archers_pre_castle", "label": "Archers before Castle", "kind": "count"},
    {"metric": "fletching_pre_castle", "label": "Got Fletching before Castle", "kind": "percent"},
    {"metric": "castle_s", "label": "Castle click", "kind": "seconds"},
]


def test_roster_counts_and_sort():
    rows = roster(RESULTS)
    by = {r["identity"]: r for r in rows}
    assert by["Alice"]["games"] == 2 and by["Alice"]["wins"] == 1 and by["Alice"]["known"] == 2
    assert by["Alice"]["win_pct"] == 50
    assert by["Bob"]["games"] == 2 and by["Bob"]["wins"] == 1 and by["Bob"]["known"] == 1
    assert by["Bob"]["win_pct"] == 100
    assert [r["identity"] for r in rows] == ["Alice", "Bob"]


def test_winners_vs_losers_averages():
    wl = winners_vs_losers(RESULTS, SPECS)
    assert wl["n_winners"] == 2 and wl["n_losers"] == 1
    f = {x["metric"]: x for x in wl["factors"]}
    assert f["archers_pre_castle"]["winners"] == 14.5
    assert f["archers_pre_castle"]["losers"] == 4.0
    assert f["fletching_pre_castle"]["winners"] == 1.0
    assert f["fletching_pre_castle"]["losers"] == 0.0
    assert f["castle_s"]["kind"] == "seconds"
