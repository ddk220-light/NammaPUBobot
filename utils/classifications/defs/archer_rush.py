"""Archer rush: the player queued >=1 foot Archer (archer line; NOT skirmisher) before the
Castle-age CLICK. Rationale: a fast-castle->crossbow player clicks Castle first, so their
archers land after the click and score zero pre-castle archers; any archer before the click
reveals aggressive-feudal intent (even a botched, low-count attempt). Rush != win — execution
is graded by factors() (Task 4)."""
from utils.classifications import gamedata as gd

W_SECONDS = 180            # "shortly after Feudal" window for the tempo factor
COMMIT_ARCHERS = 10        # the "committed" archer count for commit_to_castle_s


def _before_castle(t, castle_s):
    return t is not None and (castle_s is None or t < castle_s)


def trigger(game, pnum):
    p = gd.player(game, pnum)
    if not p or p.get("feudal_s") is None:
        return False
    castle_s = p.get("castle_s")
    return any(_before_castle(e["t_s"], castle_s) for e in gd.archer_queue_events(game, pnum))
