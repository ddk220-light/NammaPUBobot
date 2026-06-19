"""Stateful picker for player-quiz questions used by the unified scheduler. Keeps each
week fresh and varied while spreading the (limited) set of distinct player groups
evenly across the whole schedule.

Freshness model (mirrors the approved replay weekly.py granularity, plus a global
spread):
  - GLOBAL: a question_id is never posted twice; among eligible candidates the one
    whose 4-player OPTION SET has been used the FEWEST times so far wins (then the most
    exciting race). The replay corpus only yields a few dozen distinct Elo-peer
    quartets, so this is what stops the same four players recurring week after week.
  - PER WEEK: no repeated metric, answering player, or option set.
The per-week reset matters because there are only a few dozen distinct "winning"
players — a global no-repeat-answer rule would exhaust the schedule after ~30 picks.
"""
from __future__ import annotations

# player categories grouped into themes the scheduler rotates through
THEMES = {
    "Economy": ("Villagers",),
    "Age speed": ("Age speed",),
    "Buildings": ("Buildings",),
    "Army": ("Military", "Military by type"),
    "Tech": ("Tech timing",),
}
CLOSE_LO, CLOSE_HI = 0.5, 0.985


def make_player_taker(bank, blocklist=()):
    """Return (take, relaxed_count). take(theme=None, week=None) -> a fresh player
    question or None. Passing the week number resets the per-week freshness sets when it
    changes. relaxed_count() reports how many picks needed the relaxed (band-ignoring)
    fallback."""
    block = set(blocklist)
    pool = [q for q in bank if q["id"] not in block]
    used_ids = set()                                         # global: never repeat a question
    optset_uses = {}                                         # global: spread distinct 4-player groups
    wk = {"week": object(), "metrics": set(), "answers": set(), "optsets": set()}
    relaxed = [0]

    def _eligible(q, cats, strict):
        if q["id"] in used_ids:
            return False
        if cats is not None and q["category"] not in cats:
            return False
        if (q["meta"]["metric_id"] in wk["metrics"] or q["meta"]["answer"] in wk["answers"]
                or tuple(sorted(q["options"])) in wk["optsets"]):
            return False
        if strict and not (CLOSE_LO <= q["meta"]["closeness"] <= CLOSE_HI):
            return False
        return True

    def take(theme=None, week=None):
        if week != wk["week"]:
            wk.update(week=week, metrics=set(), answers=set(), optsets=set())
        cats = set(THEMES.get(theme, ())) if theme else None
        for strict in (True, False):
            best, best_key = None, None
            for idx, q in enumerate(pool):
                if not _eligible(q, cats, strict):
                    continue
                optset = tuple(sorted(q["options"]))
                key = (optset_uses.get(optset, 0), -q["meta"]["closeness"], idx)
                if best_key is None or key < best_key:
                    best, best_key, best_optset = q, key, optset
            if best is not None:
                used_ids.add(best["id"])
                wk["metrics"].add(best["meta"]["metric_id"])
                wk["answers"].add(best["meta"]["answer"])
                wk["optsets"].add(best_optset)
                optset_uses[best_optset] = optset_uses.get(best_optset, 0) + 1
                if not strict:
                    relaxed[0] += 1
                return best
        return None

    return take, (lambda: relaxed[0])
