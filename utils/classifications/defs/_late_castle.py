"""Shared builder for the late-Castle army family. Window = [3rd additional TC, Imperial click);
a rush is "made MORE THAN N of a unit type in that window". Excludes anyone already committed to an
earlier phase -- a feudal rusher (scout/archer/MAA) or an early-castle pusher (>20 of a type / >3
rams before the 3rd TC) -- since their late army continues that plan rather than being a fresh
late-castle push. Facts-only, like the other classifications."""
from utils.classifications import gamedata as gd
from utils.classifications.contract import Classification, req


def _f(x):
    return float(x) if x is not None else None


def make_late(*, key, title, count_label, unit_pred, threshold, trigger_spec, unit_source):
    def trigger(game, pnum):
        from utils.classifications.defs import _phases   # lazy import (avoids an import cycle)
        start, end = gd.late_castle_window(game, pnum)
        if start is None:
            return False
        if gd.queued_in_window(game, pnum, unit_pred, start, end) <= threshold:
            return False
        return not _phases.did_feudal_rush(game, pnum) and not _phases.committed_early(game, pnum)

    def factors(game, pnum):
        p = gd.player(game, pnum) or {}
        start, end = gd.late_castle_window(game, pnum)
        n = gd.queued_in_window(game, pnum, unit_pred, start, end) if start is not None else 0
        imp = p.get("imperial_s")
        return {
            "units_in_window": float(n),
            "third_tc_s": _f(start),
            "imperial_s": _f(imp),
            "reached_imperial": 1.0 if imp is not None else 0.0,
            "late_window_s": _f((imp - start) if (start is not None and imp is not None) else None),
        }

    factor_specs = [
        dict(metric="units_in_window", label=count_label, kind="count"),
        dict(metric="third_tc_s", label="3rd TC built (window start)", kind="seconds"),
        dict(metric="imperial_s", label="Imperial click", kind="seconds"),
        dict(metric="late_window_s", label="3rd TC->Imperial (window length)", kind="seconds"),
        dict(metric="reached_imperial", label="Reached Imperial", kind="percent"),
    ]

    return Classification(
        key=key, title=title, version=1, trigger_spec=trigger_spec,
        requirements=[
            req("late_castle_window", source="extract.players.tc_build_s[2] + imperial_s", status="available",
                note="window = [3rd additional TC build, Imperial click); tc_build_s added in extract v2"),
            req(unit_source[0], source=unit_source[1], status="available"),
            req("phase_exclusivity", source="feudal triggers + early-castle window thresholds", status="available",
                note="excluded if a feudal rusher or an early-castle pusher (>20 of a type / >3 rams pre-3rd-TC)"),
            req("winner", source="extract.players.winner", status="available",
                note="outcome dimension; consumed by the runner (shape.result_row), not trigger/factors"),
        ],
        trigger=trigger, factors=factors, factor_specs=factor_specs,
    )
