"""Scout rush: the player queued >=1 cavalry Scout (Scout Cavalry / Camel Scout; the Meso
infantry Eagle Scout and the modded 'Champi Scout' are excluded) before the Castle-age CLICK.
A fast-castle player clicks Castle first, so any scout before the click reveals aggressive-feudal
intent (even a botched, low-count attempt). Rush != win -- execution is graded by factors()."""
from utils.classifications import gamedata as gd
from utils.classifications.contract import Classification, req


def _before_castle(t, castle_s):
    return t is not None and (castle_s is None or t < castle_s)


def trigger(game, pnum):
    p = gd.player(game, pnum)
    if not p or p.get("feudal_s") is None:
        return False
    castle_s = p.get("castle_s")
    return any(_before_castle(e["t_s"], castle_s) for e in gd.scout_queue_events(game, pnum))


def _f(x):
    return float(x) if x is not None else None


def _diff(a, b):
    return (a - b) if (a is not None and b is not None) else None


def factors(game, pnum):
    """Rush-specific execution facts for a matched scout-rush player-game. Values are floats or
    None (None = didn't apply, e.g. never reached Castle / never researched Bloodlines)."""
    p = gd.player(game, pnum)
    feudal_s = p.get("feudal_s")
    castle_s = p.get("castle_s")
    scouts_pre_castle = sum((e.get("amount") or 1)
                            for e in gd.scout_queue_events(game, pnum)
                            if _before_castle(e["t_s"], castle_s))
    bloodlines_click_s = gd.tech_click_s(game, pnum, "Bloodlines")
    return {
        "scouts_pre_castle": float(scouts_pre_castle),
        "feudal_s": _f(feudal_s),
        "castle_s": _f(castle_s),
        "reached_castle": 1.0 if castle_s is not None else 0.0,
        "feudal_to_castle_s": _f(_diff(castle_s, feudal_s)),
        "bloodlines_pre_castle": 1.0 if _before_castle(bloodlines_click_s, castle_s) else 0.0,
        "bloodlines_click_s": _f(bloodlines_click_s),
    }


FACTOR_SPECS = [
    dict(metric="scouts_pre_castle", label="Scouts before Castle", kind="count"),
    dict(metric="feudal_s", label="Feudal click", kind="seconds"),
    dict(metric="castle_s", label="Castle click", kind="seconds"),
    dict(metric="bloodlines_click_s", label="Bloodlines click", kind="seconds"),
    dict(metric="bloodlines_pre_castle", label="Got Bloodlines before Castle", kind="percent"),
    dict(metric="reached_castle", label="Reached Castle Age", kind="percent"),
    dict(metric="feudal_to_castle_s", label="Time in Feudal (Feudal->Castle)", kind="seconds"),
]

CLASSIFICATION = Classification(
    key="scout_rush",
    title="Scout Rush",
    version=1,
    trigger_spec="a player who made at least one Scout Cavalry (cavalry scout line, e.g. Camel Scout; Eagle Scouts excluded) before clicking up to Castle Age",
    requirements=[
        req("scout_queue_events", source="extract.events[name in {Scout Cavalry, Camel Scout}]",
            status="available", note="cavalry scouts; Eagle Scout (Meso infantry) and Champi Scout (modded civ) excluded by name"),
        req("feudal_click_s", source="extract.players.feudal_s", status="available"),
        req("castle_click_s", source="extract.players.castle_s", status="available"),
        req("bloodlines_click_s", source="extract.techs[Bloodlines].click_s", status="available"),
        req("winner", source="extract.players.winner", status="available",
            note="outcome dimension; consumed by the runner (shape.result_row), not trigger/factors"),
    ],
    trigger=trigger,
    factors=factors,
    factor_specs=FACTOR_SPECS,
)
