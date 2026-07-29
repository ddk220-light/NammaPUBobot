# -*- coding: utf-8 -*-
"""Read + pure shaping for the per-minute eAPM series.

Kept separate from query.py so the pure helpers import cleanly under the CI shim
(no matplotlib, no DB at import time). The DB read is the only async function here.
"""
from core.database import db


def rolling_mean(values, window):
    """Trailing mean over `window` samples; the first samples average over what exists.
    Used to smooth a 1-minute-resolution series into a readable line. Pure."""
    out = []
    for i in range(len(values)):
        chunk = values[max(0, i - window + 1):i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def apm_series(rows, names):
    """rs_player_apm rows -> one zero-filled series per player.

    Every player is padded to the match's last minute so the lines share an x-axis
    and a player who was eliminated reads as falling to zero, which is the honest
    picture (see the spec's note on mgz's whole-game denominator). Pure.
    """
    if not rows:
        return []
    by_player = {}
    last = 0
    for r in rows:
        pn = r["player_number"]
        mi = int(r["minute"])
        by_player.setdefault(pn, {})[mi] = int(r["actions"] or 0)
        last = max(last, mi)
    out = []
    for pn in sorted(by_player):
        buckets = by_player[pn]
        values = [buckets.get(i, 0) for i in range(last + 1)]
        out.append(dict(
            player_number=pn,
            name=names.get(pn) or f"Player {pn}",
            minutes=list(range(last + 1)),
            values=values,
            peak=max(values),
            mean=sum(values) / len(values),
        ))
    return out


async def fetch_match_apm(aoe2_match_id):
    """Per-minute buckets for one match, ordered for apm_series."""
    rows = await db.fetchall(
        "SELECT player_number, minute, actions FROM rs_player_apm "
        "WHERE aoe2_match_id=%s ORDER BY player_number, minute",
        [aoe2_match_id])
    return rows or []
