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
N_FULL, N_MIN = 10, 5


def render_growth_curve(name, curve, days):
    """Render bot.replay_stats.query.build_growth_curve() output (the averaged production timeline)
    to a PNG. Villager + military mean lines with 95% CI bands, age-up guide lines, eco/military
    upgrade markers at avg click time, and an n-line showing how many games still contribute (the
    curve is truncated where n < N_MIN and a 'thin data' span is shaded where n < N_FULL)."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    grid = curve["grid"]
    vm, vlo, vhi = curve["vil_mean"], curve["vil_lo"], curve["vil_hi"]
    mm, mlo, mhi = curve["mil_mean"], curve["mil_lo"], curve["mil_hi"]
    n = curve["vil_n"]   # games contributing per grid point (== mil_n: both keyed on still-live games)

    keep = len(grid)                       # keep the leading run where >= N_MIN games still contribute
    for k in range(len(grid)):
        if n[k] < N_MIN:                   # n is monotonically non-increasing (games end over time)
            keep = k
            break
    if keep < 2:                           # fewer than N_MIN games even at t=0 -> show the whole low-n curve
        keep = len(grid)
    thin = keep                            # first point (after the start) where n dips below N_FULL
    for k in range(keep):
        if n[k] < N_FULL:
            thin = k
            break

    xs = [t / 60 for t in grid[:keep]]
    top = max(max((v for v in vhi[:keep] if v is not None), default=1),
              max((v for v in mhi[:keep] if v is not None), default=1)) or 1

    fig = Figure(figsize=(20, 12))
    ax = fig.subplots()
    if 0 < thin < keep:                    # shade a genuine low-confidence tail (fewer than N_FULL games)
        ax.axvspan(xs[thin], xs[-1], color="#999999", alpha=0.08, zorder=0)
        ax.text((xs[thin] + xs[-1]) / 2, top * 0.04, f"fewer than {N_FULL} games",
                ha="center", va="bottom", fontsize=11, color="#777777", style="italic", zorder=1)

    ax.fill_between(xs, vlo[:keep], vhi[:keep], color=GREEN, alpha=0.15, lw=0, zorder=1)
    ax.fill_between(xs, mlo[:keep], mhi[:keep], color=RED, alpha=0.12, lw=0, zorder=1)
    ax.plot(xs, vm[:keep], color=GREEN, lw=3, label="Villagers", zorder=3)
    ax.plot(xs, mm[:keep], color=RED, lw=3, label="Military", zorder=3)

    f, c, i = curve["ages"]
    for t, lab in ((f, "Feudal"), (c, "Castle"), (i, "Imperial")):
        if t and t / 60 <= xs[-1]:
            ax.axvline(t / 60, color="#666666", ls="--", lw=1.5, zorder=2)
            ax.text(t / 60, top * 1.005, f"{lab}  {_secs(t)}", ha="center", va="bottom",
                    fontsize=12, fontweight="bold", color="#444444", zorder=4,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.7))

    def mark(items, means, color, fc, dy):
        for j, (tech, tt) in enumerate(items):
            if tt / 60 > xs[-1]:
                continue
            y = _interp(grid[:keep], means[:keep], tt)
            if y is None:
                continue
            ax.plot([tt / 60], [y], "o", color=color, ms=8, zorder=5)
            ax.annotate(f"{tech}  {_secs(tt)}", (tt / 60, y), textcoords="offset points",
                        xytext=(7, dy + (j % 2) * 16), fontsize=10.5, zorder=6,
                        bbox=dict(boxstyle="round,pad=0.3", fc=fc, ec=color, alpha=0.92))

    mark(curve.get("eco", []), vm, GREEN, "#e9f6ee", 8)
    mark(curve.get("mil_upg", []), mm, RED, "#fbeaea", -22)

    ax.set_ylim(0, top * 1.10)
    ax.set_xlim(0, xs[-1])
    ax.set_xlabel("Game time (minutes)", fontsize=15, fontweight="bold")
    ax.set_ylabel("Average cumulative count  (villagers / military)", fontsize=15, fontweight="bold")
    ax.grid(True, ls=":", alpha=0.4)

    ax2 = ax.twinx()
    ax2.plot(xs, n[:keep], color=GREY, ls=":", lw=2, label="Games contributing (n)", zorder=2)
    ax2.set_ylim(0, curve["n"] * 1.12)
    ax2.set_ylabel("Games contributing (n)", fontsize=13, color=GREY)
    ax2.tick_params(axis="y", colors=GREY)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=14, loc="center left", framealpha=0.95)
    fig.suptitle(
        f"{name} — production timeline  ·  last {days} days  ·  {curve['n']} games\n"
        "green = villagers + economy upgrades   |   red = military + attack/armour upgrades   "
        "|   shaded = 95% CI   ·   dotted = games still in progress",
        fontsize=18, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    buf.seek(0)
    return buf
