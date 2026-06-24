from utils.classifications.contract import Classification, req


def test_req_builds_requirement():
    r = req("castle_click_s", source="extract.players.castle_s", status="available", note="age click")
    assert r == {"field": "castle_click_s", "source": "extract.players.castle_s",
                 "status": "available", "note": "age click"}


def test_classification_holds_callables_and_metadata():
    c = Classification(
        key="dummy", title="Dummy", version=1, trigger_spec="always true",
        requirements=[req("x", source="s", status="available")],
        trigger=lambda game, pnum: True,
        factors=lambda game, pnum: {"x": 1.0},
    )
    assert c.key == "dummy" and c.version == 1
    assert c.trigger({}, 1) is True
    assert c.factors({}, 1) == {"x": 1.0}
    assert c.requirements[0]["status"] == "available"
