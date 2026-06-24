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


def _f(x):
    return float(x) if x is not None else None


def _diff(a, b):
    return (a - b) if (a is not None and b is not None) else None


def factors(game, pnum):
    """Execution-quality factors for a matched archer-rush player-game. All values are
    floats or None (None = the factor didn't apply, e.g. never reached Castle)."""
    p = gd.player(game, pnum)
    feudal_s = p.get("feudal_s")
    castle_s = p.get("castle_s")
    evs = [e for e in gd.archer_queue_events(game, pnum) if _before_castle(e["t_s"], castle_s)]

    archers_pre_castle = sum((e.get("amount") or 1) for e in evs)
    first_archer_s = evs[0]["t_s"] if evs else None
    within = sum((e.get("amount") or 1) for e in evs
                 if feudal_s is not None and e["t_s"] <= feudal_s + W_SECONDS)

    fletch_click_s = gd.tech_click_s(game, pnum, "Fletching")
    fletch_pre_castle = _before_castle(fletch_click_s, castle_s)
    fletch_s = fletch_click_s if fletch_pre_castle else None

    tenth_s, cum = None, 0
    for e in evs:
        cum += (e.get("amount") or 1)
        if cum >= COMMIT_ARCHERS:
            tenth_s = e["t_s"]
            break
    commit_to_castle_s = None
    # "commit" requires BOTH the 10th archer queued AND Fletching clicked pre-castle
    # (per the definition); either alone leaves commit_to_castle_s undefined (None).
    if tenth_s is not None and fletch_s is not None and castle_s is not None:
        commit_s = max(tenth_s, fletch_s)
        if castle_s > commit_s:
            commit_to_castle_s = castle_s - commit_s

    return {
        "archers_pre_castle": float(archers_pre_castle),
        "feudal_s": _f(feudal_s),
        "castle_s": _f(castle_s),
        "reached_castle": 1.0 if castle_s is not None else 0.0,
        "feudal_to_castle_s": _f(_diff(castle_s, feudal_s)),
        "first_archer_s": _f(first_archer_s),
        "first_archer_after_feudal_s": _f(_diff(first_archer_s, feudal_s)),
        "archers_within_3min_of_feudal": float(within),
        "fletching_pre_castle": 1.0 if fletch_pre_castle else 0.0,
        "fletching_after_feudal_s": _f(_diff(fletch_s, feudal_s)),
        "commit_to_castle_s": _f(commit_to_castle_s),
        "eapm": _f(p.get("eapm")),
    }
