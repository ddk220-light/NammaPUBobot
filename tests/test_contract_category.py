from utils.classifications.contract import Classification


def _c(**kw):
    base = dict(key="k", title="t", version=1, trigger_spec="s", requirements=[],
                trigger=lambda g, p: False, factors=lambda g, p: {})
    base.update(kw)
    return Classification(**base)


def test_category_defaults_to_strategy():
    assert _c().category == "strategy"


def test_category_can_be_luck():
    assert _c(category="luck").category == "luck"
