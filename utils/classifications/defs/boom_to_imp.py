"""Boom to Imp: a greedy economic boom -- the player added at least 2 EXTRA Town Centers and made
FEWER than 20 military units (Feudal + Castle, i.e. everything before the Imperial click) before
clicking Imperial Age. Reaching Imperial is required (the boom is the path to Imperial). "Extra"
TCs = those built at/after the Feudal click (excludes a Nomad Dark-Age starting TC). Facts-only;
no exclusivity -- the <20-military condition already keeps committed rushers out."""
from utils.classifications import gamedata as gd
from utils.classifications.contract import Classification, req

MIL_LIMIT = 20
MIN_EXTRA_TC = 2


def _is_military(e):
    return bool(e.get("is_military"))


def _extra_tcs_before_imp(p):
    feudal_s = p.get("feudal_s") or 0
    imp = p.get("imperial_s")
    return [t for t in (p.get("tc_build_s") or []) if t >= feudal_s and (imp is None or t < imp)]


def _military_before_imp(game, pnum, imp):
    return gd.queued_in_window(game, pnum, _is_military, 0, imp)


def trigger(game, pnum):
    p = gd.player(game, pnum)
    if not p or p.get("imperial_s") is None:
        return False
    if len(_extra_tcs_before_imp(p)) < MIN_EXTRA_TC:
        return False
    return _military_before_imp(game, pnum, p["imperial_s"]) < MIL_LIMIT


def _f(x):
    return float(x) if x is not None else None


def factors(game, pnum):
    p = gd.player(game, pnum) or {}
    imp = p.get("imperial_s")
    return {
        "extra_tcs": float(len(_extra_tcs_before_imp(p))),
        "military_before_imp": float(_military_before_imp(game, pnum, imp)) if imp is not None else None,
        "villagers_before_imp": _f(p.get("vil_pre_imperial")),
        "feudal_s": _f(p.get("feudal_s")),
        "castle_s": _f(p.get("castle_s")),
        "imperial_s": _f(imp),
        "castle_to_imp_s": _f((imp - p["castle_s"]) if (imp is not None and p.get("castle_s") is not None) else None),
    }


FACTOR_SPECS = [
    dict(metric="extra_tcs", label="Extra TCs before Imp", kind="count"),
    dict(metric="military_before_imp", label="Military before Imp", kind="count"),
    dict(metric="villagers_before_imp", label="Villagers before Imp", kind="count"),
    dict(metric="imperial_s", label="Imperial click", kind="seconds"),
    dict(metric="castle_to_imp_s", label="Castle->Imp (time in Castle)", kind="seconds"),
    dict(metric="feudal_s", label="Feudal click", kind="seconds"),
    dict(metric="castle_s", label="Castle click", kind="seconds"),
]

CLASSIFICATION = Classification(
    key="boom_to_imp", title="Boom to Imp", version=1,
    trigger_spec="a player who built at least 2 extra Town Centers and made fewer than 20 military units (Feudal + Castle) before clicking Imperial Age",
    requirements=[
        req("extra_tc_count", source="extract.players.tc_build_s (>=feudal, <imperial)", status="available"),
        req("military_before_imp", source="extract.events[is_military, t<imperial]", status="available"),
        req("imperial_click_s", source="extract.players.imperial_s", status="available"),
        req("villagers_before_imp", source="extract.players.vil_pre_imperial", status="available",
            note="boom-size context for the report"),
        req("winner", source="extract.players.winner", status="available",
            note="outcome dimension; consumed by the runner (shape.result_row), not trigger/factors"),
    ],
    trigger=trigger, factors=factors, factor_specs=FACTOR_SPECS,
)
