from utils.classifications.contract import Classification, req
from utils.classifications.registry import REGISTRY


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


def test_registry_contains_archer_rush():
    assert "archer_rush" in REGISTRY
    c = REGISTRY["archer_rush"]
    assert c.title == "Archer Rush"
    assert callable(c.trigger) and callable(c.factors)


def test_archer_rush_requirements_all_available():
    c = REGISTRY["archer_rush"]
    assert c.requirements, "archer_rush must declare its data requirements"
    assert len(c.requirements) == 5
    assert all(r["status"] == "available" for r in c.requirements)


def test_archer_rush_factor_specs():
    c = REGISTRY["archer_rush"]
    metrics = {s["metric"] for s in c.factor_specs}
    assert {"archers_pre_castle", "fletching_pre_castle", "castle_s"} <= metrics
    assert all({"metric", "label", "kind"} <= set(s) for s in c.factor_specs)
    assert all(s["kind"] in ("count", "seconds", "percent") for s in c.factor_specs)
