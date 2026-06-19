"""Stateful picker for player-quiz questions used by the unified scheduler. Favors
exciting races (closeness band) and never repeats a metric or an answering player
within one draw. Mirrors sample_weeks' taker shape so build_schedule can treat the
two sources symmetrically."""
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
    """Return (take, relaxed_count). take(theme=None) -> a fresh player question or None.
    relaxed_count() reports how many picks needed the relaxed (band-ignoring) fallback."""
    block = set(blocklist)
    pool = [q for q in bank if q["id"] not in block]
    pool.sort(key=lambda q: -q["meta"]["closeness"])         # most exciting first
    used_ids, used_metrics, used_answers = set(), set(), set()
    relaxed = [0]

    def take(theme=None):
        cats = set(THEMES.get(theme, ())) if theme else None
        for strict in (True, False):
            for q in pool:
                if q["id"] in used_ids:
                    continue
                if cats is not None and q["category"] not in cats:
                    continue
                if q["meta"]["metric_id"] in used_metrics or q["meta"]["answer"] in used_answers:
                    continue
                if strict and not (CLOSE_LO <= q["meta"]["closeness"] <= CLOSE_HI):
                    continue
                used_ids.add(q["id"])
                used_metrics.add(q["meta"]["metric_id"])
                used_answers.add(q["meta"]["answer"])
                if not strict:
                    relaxed[0] += 1
                return q
        return None

    return take, (lambda: relaxed[0])
