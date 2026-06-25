"""Shared builder for the Early-Castle rush family. Each rush is "the player made MORE THAN N of
a unit type within the early-Castle window [Castle click, 3rd additional TC)". They share the
window + factor shape and differ only in (unit predicate, threshold, signature upgrade tech).
Each defs/<x>_rush.py is a thin wrapper around make(). Facts-only, like the other classifications.

NOTE (queue-name reality, same as maa_rush): the Stable/Range *train* the base unit ("Knight",
"Archer", "Cavalry Archer", "Camel Rider") -- Cavalier/Crossbowman/Heavy Cav Archer/etc. are
UPGRADE techs that transform existing units, never trained directly. So a rush is counted by the
base queued unit in the window, and the unit-line upgrade is tracked as the signature factor."""
from utils.classifications import gamedata as gd
from utils.classifications.contract import Classification, req


def _f(x):
    return float(x) if x is not None else None


def make(*, key, title, count_label, unit_pred, threshold, sig_tech, trigger_spec, unit_source):
    def trigger(game, pnum):
        from utils.classifications.defs import _phases   # lazy import (avoids an import cycle)
        start, end = gd.early_castle_window(game, pnum)
        if start is None:
            return False
        if gd.queued_in_window(game, pnum, unit_pred, start, end) <= threshold:
            return False
        return not _phases.did_feudal_rush(game, pnum)   # a feudal rusher's castle army is a continuation

    def factors(game, pnum):
        p = gd.player(game, pnum) or {}
        start, end = gd.early_castle_window(game, pnum)
        n = gd.queued_in_window(game, pnum, unit_pred, start, end)
        sig_in, sig_click = gd.tech_in_window(game, pnum, sig_tech, start, end)
        built_3rd = end is not None
        return {
            "units_in_window": float(n),
            "castle_s": _f(start),
            "built_3rd_tc": 1.0 if built_3rd else 0.0,
            "castle_to_3rd_tc_s": _f((end - start) if (built_3rd and start is not None) else None),
            "sig_upgrade_in_window": sig_in,
            "sig_upgrade_click_s": sig_click,
            "reached_imperial": 1.0 if p.get("imperial_s") is not None else 0.0,
        }

    factor_specs = [
        dict(metric="units_in_window", label=count_label, kind="count"),
        dict(metric="castle_s", label="Castle click", kind="seconds"),
        dict(metric="castle_to_3rd_tc_s", label="Castle->3rd TC (window length)", kind="seconds"),
        dict(metric="built_3rd_tc", label="Built a 3rd extra TC (boomed)", kind="percent"),
        dict(metric="sig_upgrade_in_window", label="Got {} in window".format(sig_tech), kind="percent"),
        dict(metric="sig_upgrade_click_s", label="{} click".format(sig_tech), kind="seconds"),
        dict(metric="reached_imperial", label="Reached Imperial", kind="percent"),
    ]

    sig_field = sig_tech.lower().replace(" ", "_").replace("-", "_") + "_click_s"
    return Classification(
        key=key, title=title, version=1, trigger_spec=trigger_spec,
        requirements=[
            req("early_castle_window", source="extract.players.castle_s + tc_build_s (>=feudal)[1]",
                status="available",
                note="window = [Castle click, 3rd additional TC build); tc_build_s added in extract v2"),
            req(unit_source[0], source=unit_source[1], status="available"),
            req("castle_click_s", source="extract.players.castle_s", status="available"),
            req(sig_field, source="extract.techs[{}].click_s".format(sig_tech), status="available",
                note="signature unit-line upgrade for this rush"),
            req("winner", source="extract.players.winner", status="available",
                note="outcome dimension; consumed by the runner (shape.result_row), not trigger/factors"),
        ],
        trigger=trigger, factors=factors, factor_specs=factor_specs,
    )
