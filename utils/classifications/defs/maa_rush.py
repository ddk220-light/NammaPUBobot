"""MAA rush: the player queued >=1 Militia-line unit (Militia, or the Sicilian Serjeant) before
the Castle-age CLICK. Spearmen are a separate category and never count. NOTE: the militia line is
only ever *produced* as 'Militia' -- Man-at-Arms / Long Swordsman / Champion are upgrades, not
trained units -- so the Man-at-Arms upgrade timing is a FACTOR (not a trigger gate): it separates
a committed MAA rush from incidental dark-age militia. Rush != win -- execution is graded by
factors()."""
from utils.classifications import gamedata as gd
from utils.classifications.contract import Classification, req


def _before_castle(t, castle_s):
    return t is not None and (castle_s is None or t < castle_s)


def trigger(game, pnum):
    p = gd.player(game, pnum)
    if not p or p.get("feudal_s") is None:
        return False
    castle_s = p.get("castle_s")
    return any(_before_castle(e["t_s"], castle_s) for e in gd.militia_queue_events(game, pnum))


def _f(x):
    return float(x) if x is not None else None


def _diff(a, b):
    return (a - b) if (a is not None and b is not None) else None


def factors(game, pnum):
    """Rush-specific execution facts for a matched MAA-rush player-game. Values are floats or
    None (None = didn't apply, e.g. never reached Castle / never researched the Man-at-Arms
    upgrade)."""
    p = gd.player(game, pnum)
    feudal_s = p.get("feudal_s")
    castle_s = p.get("castle_s")
    militia_pre_castle = sum((e.get("amount") or 1)
                             for e in gd.militia_queue_events(game, pnum)
                             if _before_castle(e["t_s"], castle_s))
    maa_click_s = gd.tech_click_s(game, pnum, "Man-at-Arms")
    return {
        "militia_pre_castle": float(militia_pre_castle),
        "feudal_s": _f(feudal_s),
        "castle_s": _f(castle_s),
        "reached_castle": 1.0 if castle_s is not None else 0.0,
        "feudal_to_castle_s": _f(_diff(castle_s, feudal_s)),
        "maa_upgrade_pre_castle": 1.0 if _before_castle(maa_click_s, castle_s) else 0.0,
        "maa_upgrade_click_s": _f(maa_click_s),
    }


FACTOR_SPECS = [
    dict(metric="militia_pre_castle", label="Militia/Serjeant before Castle", kind="count"),
    dict(metric="feudal_s", label="Feudal click", kind="seconds"),
    dict(metric="castle_s", label="Castle click", kind="seconds"),
    dict(metric="maa_upgrade_click_s", label="Man-at-Arms upgrade click", kind="seconds"),
    dict(metric="maa_upgrade_pre_castle", label="Got Man-at-Arms before Castle", kind="percent"),
    dict(metric="reached_castle", label="Reached Castle Age", kind="percent"),
    dict(metric="feudal_to_castle_s", label="Time in Feudal (Feudal->Castle)", kind="seconds"),
]

CLASSIFICATION = Classification(
    key="maa_rush",
    title="MAA Rush",
    version=1,
    trigger_spec="a player who made at least one Militia-line unit (Militia/Man-at-Arms, or Serjeant; spearmen excluded) before clicking up to Castle Age",
    requirements=[
        req("militia_queue_events", source="extract.events[category=militia_line minus Flemish Militia, plus Serjeant]",
            status="available", note="militia line + Sicilian Serjeant; spearman_line is a separate category, never included"),
        req("feudal_click_s", source="extract.players.feudal_s", status="available"),
        req("castle_click_s", source="extract.players.castle_s", status="available"),
        req("man_at_arms_upgrade_click_s", source="extract.techs[Man-at-Arms].click_s", status="available"),
        req("winner", source="extract.players.winner", status="available",
            note="outcome dimension; consumed by the runner (shape.result_row), not trigger/factors"),
    ],
    trigger=trigger,
    factors=factors,
    factor_specs=FACTOR_SPECS,
)
