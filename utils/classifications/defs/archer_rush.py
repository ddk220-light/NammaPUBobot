"""Archer rush: the player queued >=1 foot Archer (archer line; NOT skirmisher) before the
Castle-age CLICK. Rationale: a fast-castle->crossbow player clicks Castle first, so their
archers land after the click and score zero pre-castle archers; any archer before the click
reveals aggressive-feudal intent (even a botched, low-count attempt). Rush != win — execution
is graded by factors() (Task 4)."""
from utils.classifications import gamedata as gd
from utils.classifications.contract import Classification, req

def _before_castle(t, castle_s):
    return t is not None and (castle_s is None or t < castle_s)


def trigger(game, pnum):
    p = gd.player(game, pnum)
    if not p or p.get("feudal_s") is None:
        return False
    castle_s = p.get("castle_s")
    return any(_before_castle(e["t_s"], castle_s) for e in gd.archer_queue_events(game, pnum))


def _f(x):
    return float(x) if x is not None else None


def _diff(a, b):
    return (a - b) if (a is not None and b is not None) else None


def factors(game, pnum):
    """Rush-specific execution facts for a matched archer-rush player-game. Values are floats or
    None (None = didn't apply, e.g. never reached Castle / never researched Fletching)."""
    p = gd.player(game, pnum)
    feudal_s = p.get("feudal_s")
    castle_s = p.get("castle_s")
    archers_pre_castle = sum((e.get("amount") or 1)
                             for e in gd.archer_queue_events(game, pnum)
                             if _before_castle(e["t_s"], castle_s))
    fletching_click_s = gd.tech_click_s(game, pnum, "Fletching")
    return {
        "archers_pre_castle": float(archers_pre_castle),
        "feudal_s": _f(feudal_s),
        "castle_s": _f(castle_s),
        "reached_castle": 1.0 if castle_s is not None else 0.0,
        "feudal_to_castle_s": _f(_diff(castle_s, feudal_s)),
        "fletching_pre_castle": 1.0 if _before_castle(fletching_click_s, castle_s) else 0.0,
        "fletching_click_s": _f(fletching_click_s),
    }


FACTOR_SPECS = [
    dict(metric="archers_pre_castle", label="Archers before Castle", kind="count"),
    dict(metric="feudal_s", label="Feudal click", kind="seconds"),
    dict(metric="castle_s", label="Castle click", kind="seconds"),
    dict(metric="fletching_click_s", label="Fletching click", kind="seconds"),
    dict(metric="fletching_pre_castle", label="Got Fletching before Castle", kind="percent"),
    dict(metric="reached_castle", label="Reached Castle Age", kind="percent"),
    dict(metric="feudal_to_castle_s", label="Time in Feudal (Feudal->Castle)", kind="seconds"),
]

CLASSIFICATION = Classification(
    key="archer_rush",
    title="Archer Rush",
    version=2,
    trigger_spec="a player who made at least one foot Archer (not Skirmisher) before clicking up to Castle Age",
    requirements=[
        req("foot_archer_queue_events", source="extract.events[category=archer_line]",
            status="available", note="per-queue timestamps; emitted by extract.py:147"),
        req("feudal_click_s", source="extract.players.feudal_s", status="available"),
        req("castle_click_s", source="extract.players.castle_s", status="available"),
        req("fletching_click_s", source="extract.techs[Fletching].click_s", status="available"),
        req("winner", source="extract.players.winner", status="available",
            note="outcome dimension; consumed by the runner (shape.result_row), not trigger/factors"),
    ],
    trigger=trigger,
    factors=factors,
    factor_specs=FACTOR_SPECS,
)
