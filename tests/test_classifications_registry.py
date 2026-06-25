"""Structural invariants every registered classification must satisfy. Auto-covers new defs
(scout_rush, maa_rush, ...) without per-classification boilerplate."""
import pytest

from utils.classifications.registry import REGISTRY

CLASSIFICATIONS = list(REGISTRY.values())
IDS = [c.key for c in CLASSIFICATIONS]


def test_registry_keys_match_classification_keys():
    assert all(key == c.key for key, c in REGISTRY.items())


def test_expected_use_cases_present():
    assert {"archer_rush", "scout_rush", "maa_rush"} <= set(REGISTRY)


@pytest.mark.parametrize("c", CLASSIFICATIONS, ids=IDS)
def test_classification_is_well_formed(c):
    assert c.key and c.title and c.trigger_spec
    assert isinstance(c.version, int) and c.version >= 1
    assert callable(c.trigger) and callable(c.factors)
    assert c.requirements, "must declare data requirements"
    assert all(r["status"] in ("available", "missing") for r in c.requirements)


@pytest.mark.parametrize("c", CLASSIFICATIONS, ids=IDS)
def test_factor_specs_well_formed(c):
    for s in c.factor_specs:
        assert {"metric", "label", "kind"} <= set(s)
        assert s["kind"] in ("count", "seconds", "percent")
    metrics = [s["metric"] for s in c.factor_specs]
    assert len(metrics) == len(set(metrics)), "duplicate metric in factor_specs"


@pytest.mark.parametrize("c", CLASSIFICATIONS, ids=IDS)
def test_factor_specs_metrics_are_produced_by_factors(c):
    # Every advertised factor must actually be emitted by factors() on a minimal game where the
    # player exists but did nothing -- guards against typos between factors() and FACTOR_SPECS.
    game = {"players": [{"player_number": 1, "feudal_s": 600, "castle_s": 1200}],
            "techs": [], "events": []}
    produced = set(c.factors(game, 1))
    advertised = {s["metric"] for s in c.factor_specs}
    assert advertised <= produced, "factor_specs metric(s) not returned by factors(): {}".format(
        advertised - produced)
