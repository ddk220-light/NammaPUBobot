# -*- coding: utf-8 -*-
"""matplotlib renderer for the /player_details build-timeline chart. Headless (Agg) + the OO
Figure API (no pyplot global state, safe under the async bot) — mirrors bot/player_profile.py.
matplotlib is imported lazily inside render_timeline so importing this module stays cheap and
test-safe (CI has no matplotlib)."""
import io


def _secs(v):
    return f"{int(v) // 60}:{int(v) % 60:02d}"


def _interp(grid, ys, t):
    """Linear interpolation of ys (defined over grid seconds) at time t seconds — places an
    upgrade marker on the averaged line at its average click time."""
    if not grid:
        return None
    if t <= grid[0]:
        return ys[0]
    if t >= grid[-1]:
        return ys[-1]
    for k in range(1, len(grid)):
        if t <= grid[k]:
            x0, x1, y0, y1 = grid[k - 1], grid[k], ys[k - 1], ys[k]
            if y0 is None or y1 is None or x1 == x0:
                return y1 if y0 is None else y0
            return y0 + (y1 - y0) * (t - x0) / (x1 - x0)
    return ys[-1]


def render_timeline(name, data, days):
    """Render bot.replay_stats.query.build_timeline() output to a PNG. Returns an io.BytesIO."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    vil, mil = data["vil"], data["mil"]
    f, c, i = data["ages"]
    eco, milu = data["eco"], data["mil_upg"]
    x = [0, 1, 2, 3]
    w = 0.40
    xv = [k - w / 2 for k in x]
    xm = [k + w / 2 for k in x]
    top = max(max((v for v in vil if v is not None), default=0),
              max((v for v in mil if v is not None), default=0)) or 1

    fig = Figure(figsize=(20, 12))
    ax = fig.subplots()
    ax.bar(xv, [v or 0 for v in vil], w, color="#2e8b57", alpha=0.9, label="Villagers", zorder=2)
    ax.bar(xm, [v or 0 for v in mil], w, color="#b22222", alpha=0.9, label="Military", zorder=2)

    off, step = top * 0.10, top * 0.092
    stack_tops = []

    def stack(xc, barh, items, fc, ec):
        base = (barh or 0) + off
        for j, (tech, tt) in enumerate(items):   # earliest just above the bar, later ones higher
            ax.text(xc, base + j * step, f"{tech}  {_secs(tt)}", ha="center", va="bottom",
                    fontsize=11.5, zorder=4,
                    bbox=dict(boxstyle="round,pad=0.3", fc=fc, ec=ec, alpha=0.92))
        stack_tops.append((barh or 0) + off + len(items) * step)

    for k in x:
        ax.text(xv[k], (vil[k] or 0) + top * 0.015, f"{vil[k] or 0:.0f}", ha="center", va="bottom",
                fontsize=14, fontweight="bold", color="#2e8b57", zorder=3)
        ax.text(xm[k], (mil[k] or 0) + top * 0.015, f"{mil[k] or 0:.0f}", ha="center", va="bottom",
                fontsize=14, fontweight="bold", color="#b22222", zorder=3)
        stack(xv[k], vil[k], eco[k], "#e9f6ee", "#2e8b57")
        stack(xm[k], mil[k], milu[k], "#fbeaea", "#b22222")

    ax.set_ylim(0, (max(stack_tops) if stack_tops else top) * 1.07)
    ax.set_xlim(-0.6, 3.6)
    ax.set_xticks(x)
    xlabels = [f"Before\nFeudal\n(→ {_secs(f)})" if f else "Before\nFeudal",
               f"Before\nCastle\n(→ {_secs(c)})" if c else "Before\nCastle",
               f"Before\nImperial\n(→ {_secs(i)})" if i else "Before\nImperial",
               "Post-\nImperial"]
    ax.set_xticklabels(xlabels, fontsize=14, fontweight="bold")
    ax.set_ylabel("Average count  (villagers / military)", fontsize=15, fontweight="bold")
    ax.grid(axis="y", ls=":", alpha=0.4)
    for xb in (0.5, 1.5, 2.5):
        ax.axvline(xb, color="#cccccc", ls="--", lw=1, zorder=1)
    ax.legend(fontsize=15, loc="upper left", framealpha=0.95)
    fig.suptitle(f"{name} — build timeline  ·  last {days} days  ·  {data['n']} ranked games\n"
                 "green = villagers + economy upgrades   |   red = military + attack/armour upgrades",
                 fontsize=18, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    buf.seek(0)
    return buf


GREEN, RED, GREY = "#2e8b57", "#b22222", "#808080"
P1C, P2C = "#1f6feb", "#e8710a"   # per-player accent for age-up guides (distinct from green/red)
N_FULL, N_MIN = 10, 5


def _decollide(fig, labels, fixed, pad=3.0):
    """Greedy label de-collision: push each annotation in `labels` (a list of (artist, x_anchor))
    straight up until its rendered box overlaps neither the others nor the `fixed` texts. Run AFTER
    layout so the window extents are final; each label's leader line stretches to follow it. Offsets
    are nudged in points (physical), so the result is dpi-independent at save time."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    rend = canvas.get_renderer()
    placed = [list(t.get_window_extent(rend).extents) for t in fixed]
    for a, _xa in sorted(labels, key=lambda u: u[1]):       # left to right
        e = a.get_window_extent(rend)
        box = [e.x0, e.y0, e.x1, e.y1]
        moved = 0.0
        for _ in range(len(placed) + 2):
            hit = next((p for p in placed if box[0] < p[2] and box[2] > p[0]
                        and box[1] < p[3] and box[3] > p[1]), None)
            if hit is None:
                break
            shift = hit[3] - box[1] + pad                   # lift clear of the box it overlaps
            box[1] += shift
            box[3] += shift
            moved += shift
        if moved:
            dx, dy = a.xyann
            a.xyann = (dx, dy + moved * 72.0 / fig.dpi)      # px -> points
        placed.append(box)


def _short(s, nmax=16):
    s = s or "?"
    return s if len(s) <= nmax else s[:nmax - 1] + "."


def _keep_thin(n):
    """keep = leading run where >= N_MIN games still contribute (truncate the thin tail; n is
    non-increasing). thin = first point where n dips below N_FULL. Falls back to the whole curve
    when there are fewer than N_MIN games even at t=0."""
    keep = len(n)
    for k in range(len(n)):
        if n[k] < N_MIN:
            keep = k
            break
    if keep < 2:
        keep = len(n)
    thin = keep
    for k in range(keep):
        if n[k] < N_FULL:
            thin = k
            break
    return keep, thin


DOT = (0, (1.4, 1.5))   # player-2 / secondary linestyle


# The in-game AoE2 player-slot colours, keyed by player_number: 1 blue, 2 red, 3 green,
# 4 yellow, 5 teal, 6 purple, 7 grey, 8 orange. Using the slot colour instead of an
# invented palette means a player finds their own line by the colour they already played
# as — and regulars who always take the same slot learn it once. Yellow and grey are
# darkened from their in-game values, which are near-invisible on a white plot.
_APM_PLAYER_COLOURS = {
    1: "#2e5fd9",   # blue
    2: "#d6342c",   # red
    3: "#2e9e36",   # green
    4: "#c9a200",   # yellow
    5: "#1fa8ad",   # teal
    6: "#8e44c4",   # purple
    7: "#6e6e6e",   # grey
    8: "#e07b18",   # orange
}
_APM_UNKNOWN_COLOUR = "#9a9a9a"

# Colour now identifies the player, so team moves onto the dash pattern. Validating against
# 9 real 8-player matches, eight distinct hues separate cleanly on their own — the earlier
# blur came from two hue families having to carry four players each. Team stays visible
# without spending hue on it, and no data is hidden (unlike team-averaging or a top-4 cut).
_APM_TEAM_LINESTYLES = {0: "-", 1: "--"}
_APM_UNKNOWN_LINESTYLE = ":"

# Codepoint ranges matplotlib's bundled DejaVu Sans has no glyphs for, and which the
# production image (python:3.11-slim) ships no system font to cover either — they render as
# empty "tofu" boxes. A CJK player name like 一般般 reproduces this 100% of the time. Latin,
# Greek and Cyrillic are all in DejaVu and are left verbatim.
_NO_GLYPH_RANGES = (
    (0x2E80, 0x9FFF),      # CJK radicals/Kangxi/symbols, Kana, Bopomofo, CJK Unified + Ext A
    (0xA960, 0xA97F),      # Hangul Jamo Extended-A
    (0xAC00, 0xD7FF),      # Hangul syllables
    (0xF900, 0xFAFF),      # CJK compatibility ideographs
    (0xFE30, 0xFE4F),      # CJK compatibility forms
    (0xFF01, 0xFF60),      # halfwidth/fullwidth forms
    (0x10000, 0x10FFFF),   # astral plane: emoji, CJK Ext B+
)


def _has_glyph(ch):
    cp = ord(ch)
    return not any(lo <= cp <= hi for lo, hi in _NO_GLYPH_RANGES)


def _apm_label(name, player_number, nmax=16):
    """Legend name for the APM chart only. Drops characters the render font cannot draw, so
    they show as nothing rather than as tofu boxes; falls back to the player slot when that
    leaves nothing at all. Deliberately NOT folded into _short(), which the timeline and
    growth-curve renderers use — those label a single named player the caller chose, and must
    keep rendering that name verbatim. Pure."""
    kept = "".join(c for c in (name or "") if _has_glyph(c)).strip()
    return _short(kept, nmax) if kept else f"Player {player_number}"


def _apm_line_style(player_number, team):
    """(colour, linestyle) for one player's line: colour is their in-game slot colour, dash
    pattern is their team. A slot outside 1-8 (or an unmapped team) falls back to a neutral
    grey (or dotted) rather than colliding with a real player's colour. Pure."""
    return (_APM_PLAYER_COLOURS.get(player_number, _APM_UNKNOWN_COLOUR),
            _APM_TEAM_LINESTYLES.get(team, _APM_UNKNOWN_LINESTYLE))


def rolling_mean(values, window):
    """Trailing mean over `window` samples; the first samples average over what exists.
    Used to smooth a 1-minute-resolution series into a readable line. Pure.

    Lives here rather than in apm_query so the renderer has no path to the DB layer:
    apm_query does `from nammaoe2bot.runtime.database import db` at module scope, and importing it from
    the chart worker only ever worked because fork inherits the parent's sys.modules.
    """
    window = max(1, int(window))       # a non-positive window would divide by zero
    out = []
    for i in range(len(values)):
        chunk = values[max(0, i - window + 1):i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def render_apm_curve(series, teams, smooth=3):
    """Per-minute eAPM over the course of a match, one line per player.

    `series` is bot.replay_stats.apm_query.apm_series output; `teams` maps
    player_number -> 0/1. Lines are smoothed with a trailing rolling mean because a
    1-minute-resolution 8-player chart is unreadable raw -- peaks are reported as
    numbers on the card instead. Colour is the player's in-game slot colour so they can
    find themselves by the colour they played as; the dash pattern carries their team.
    Returns a BytesIO PNG.
    """
    import io

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    fig = Figure(figsize=(14, 7))
    ax = fig.subplots()

    for s in series:
        colour, linestyle = _apm_line_style(s["player_number"], teams.get(s["player_number"]))
        ax.plot(s["minutes"], rolling_mean(s["values"], smooth),
                label=f"{_apm_label(s['name'], s['player_number'])} (peak {s['peak']})",
                color=colour, linestyle=linestyle, linewidth=2.0)

    ax.set_xlabel("Minute")
    ax.set_ylabel("Actions per minute (eAPM)")
    ax.set_title(f"Activity over the match · {smooth}-minute rolling average")
    ax.grid(True, alpha=0.2)
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=9, ncol=2)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    buf.seek(0)
    return buf
