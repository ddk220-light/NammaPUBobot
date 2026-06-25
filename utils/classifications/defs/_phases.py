"""Phase-exclusivity helpers. A player is attributed to their EARLIEST aggression phase, so later
phases exclude anyone already committed earlier (their later army is a continuation of that plan,
not a separate push):

  feudal rush (scout/archer/MAA)  -->  excluded from the early-castle rushes
  feudal rush OR early-castle rush -->  excluded from the late-castle family

Imported LAZILY by the early/late builders (inside trigger calls) because the rush defs import
those builders -- a module-level import here would create a cycle."""
from utils.classifications import gamedata as gd


def did_feudal_rush(game, pnum):
    """True if the player triggered any feudal rush: scout / archer / MAA."""
    from utils.classifications.defs import archer_rush, maa_rush, scout_rush
    return (scout_rush.trigger(game, pnum) or archer_rush.trigger(game, pnum)
            or maa_rush.trigger(game, pnum))


def committed_early(game, pnum):
    """True if the player triggered any early-castle rush -- >20 of a unit type (knight / foot
    archer / cav archer / camel) or >3 rams in the early-Castle window [Castle click, 3rd TC).
    Uses the RAW unit thresholds (independent of the feudal-rush exclusion)."""
    from utils.classifications.defs import (camel_rush, cav_archer_rush, crossbow_rush,
                                            knight_rush, ram_push)
    start, end = gd.early_castle_window(game, pnum)
    if start is None:
        return False
    checks = [(knight_rush._is_knight, 20), (crossbow_rush._is_foot_archer, 20),
              (cav_archer_rush._is_cav_archer, 20), (camel_rush._is_camel, 20),
              (ram_push._is_ram, 3)]
    return any(gd.queued_in_window(game, pnum, pred, start, end) > thr for pred, thr in checks)
