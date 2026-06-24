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


def render_growth_curve(name, curve, days, curve2=None, name2=None):
    """Render the averaged production timeline to a PNG. Single player: villager + military mean
    lines with 95% CI bands, age-up guides, eco/military upgrade markers, and an n-line. When a
    second curve is given, overlay player 2 as DOTTED lines for comparison: the CI bands are
    dropped, but BOTH players keep their eco/military research markers (P2 drawn with a hollow dot
    and a dashed box/leader so it reads as the compared player) and BOTH players' age-ups are drawn
    as vertical guides coloured per player (P1 blue, P2 orange) plus a colour-matched corner table —
    so it's easy to see who clicks each age / researches each upgrade earlier."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    compare = curve2 is not None

    def _mx(seq):
        return max((v for v in seq if v is not None), default=1)

    grid = curve["grid"]
    vm, vlo, vhi = curve["vil_mean"], curve["vil_lo"], curve["vil_hi"]
    mm, mlo, mhi = curve["mil_mean"], curve["mil_lo"], curve["mil_hi"]
    n = curve["vil_n"]
    keep, thin = _keep_thin(n)
    xs = [t / 60 for t in grid[:keep]]
    # Scale y to the confidence-reliable prefix (n >= N_FULL) + the full mean lines, not the wide
    # thin-tail CI (which would squash the curves; the tail band is already shaded low-confidence).
    rel = thin if 0 < thin < keep else keep
    top = max(_mx(vhi[:rel]), _mx(mhi[:rel]), _mx(vm[:keep]), _mx(mm[:keep]))
    if compare:
        n2 = curve2["vil_n"]
        keep2, _ = _keep_thin(n2)
        xs2 = [t / 60 for t in curve2["grid"][:keep2]]
        vm2, mm2 = curve2["vil_mean"], curve2["mil_mean"]
        top = max(top, _mx(vm2[:keep2]), _mx(mm2[:keep2]))
    top = top or 1
    ymax = top * (1.30 if compare else 1.18)   # extra headroom: compare stacks two players' labels

    fig = Figure(figsize=(20, 12))
    ax = fig.subplots()

    if not compare:
        if 0 < thin < keep:                # shade a genuine low-confidence tail
            ax.axvspan(xs[thin], xs[-1], color="#999999", alpha=0.08, zorder=0)
            ax.text((xs[thin] + xs[-1]) / 2, top * 0.04, f"fewer than {N_FULL} games",
                    ha="center", va="bottom", fontsize=11, color="#777777", style="italic", zorder=1)
        ax.fill_between(xs, vlo[:keep], vhi[:keep], color=GREEN, alpha=0.15, lw=0, zorder=1)
        ax.fill_between(xs, mlo[:keep], mhi[:keep], color=RED, alpha=0.12, lw=0, zorder=1)

    ax.plot(xs, vm[:keep], color=GREEN, lw=3, zorder=4,
            label=f"{_short(name)} villagers" if compare else "Villagers")
    ax.plot(xs, mm[:keep], color=RED, lw=3, zorder=4,
            label=f"{_short(name)} military" if compare else "Military")
    if compare:
        ax.plot(xs2, vm2[:keep2], color=GREEN, lw=2.5, ls=DOT, zorder=4, label=f"{_short(name2)} villagers")
        ax.plot(xs2, mm2[:keep2], color=RED, lw=2.5, ls=DOT, zorder=4, label=f"{_short(name2)} military")

    age_texts, upgrades = [], []

    # upgrade markers: a dot on the line + a leader-lined label, de-collided after layout so
    # densely-clustered researches (or two players' at the same avg time) never overlap. The dashed
    # variant (P2 in compare mode) uses a hollow dot + dashed box/leader so it reads as "compared".
    def add_marks(items, grid_, means, xmax, color, fc, dashed):
        ls = "--" if dashed else "-"
        for tech, tt in items:
            x = tt / 60
            if x > xmax:
                continue
            y = _interp(grid_, means, tt)
            if y is None:
                continue
            ax.plot([x], [y], marker="o", ls="none", ms=7, zorder=5,
                    mfc=("none" if dashed else color), mec=color, mew=(1.6 if dashed else 0))
            upgrades.append((ax.annotate(
                f"{tech}  {_secs(tt)}", (x, y), textcoords="offset points", xytext=(0, 12),
                ha="center", fontsize=(9 if compare else 10), zorder=6,
                bbox=dict(boxstyle="round,pad=0.3", fc=fc, ec=color, alpha=0.95, ls=ls),
                arrowprops=dict(arrowstyle="-", color=color, lw=0.7, alpha=0.5, ls=ls,
                                shrinkA=0, shrinkB=1)), x))

    if compare:
        # (B) age-up guides coloured per player (P1 blue/solid, P2 orange/dashed) so the earlier
        # click of each age is obvious; the neutral phase name sits centred between the pair.
        for cv, pc, st, xmax in ((curve, P1C, "-", xs[-1]), (curve2, P2C, "--", xs2[-1])):
            for t in cv["ages"]:
                if t and t / 60 <= xmax:
                    ax.axvline(t / 60, color=pc, ls=st, lw=1.7, alpha=0.85, zorder=2)
        for idx, lab in enumerate(("Feudal", "Castle", "Imperial")):
            ts = [t for t, xm in ((curve["ages"][idx], xs[-1]), (curve2["ages"][idx], xs2[-1]))
                  if t and t / 60 <= xm]
            if ts:
                age_texts.append(ax.text(
                    sum(ts) / len(ts) / 60, ymax * 0.985, lab, ha="center", va="top",
                    fontsize=11, fontweight="bold", color="#555555", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#cccccc", alpha=0.9)))

        # exact age times — each player's row in their accent colour (so colour -> player is legible).
        # One aligned monospace block gives the box; the two data rows are overlaid in colour on top.
        def _row(nm, cv):
            return _short(nm, 14).ljust(14) + "".join(
                (_secs(t) if t else "-").rjust(9) for t in cv["ages"])
        hdr = "age-up".ljust(14) + "Feudal".rjust(9) + "Castle".rjust(9) + "Imperial".rjust(9)
        r1, r2 = _row(name, curve), _row(name2, curve2)
        tx, ty = 0.987, 0.987
        age_texts.append(ax.text(
            tx, ty, hdr + "\n" + r1 + "\n" + r2, transform=ax.transAxes, ha="right", va="top",
            fontfamily="monospace", fontsize=11, color="#333333", zorder=8,
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="#bdbdbd", alpha=0.96)))
        ax.text(tx, ty, "\n" + r1, transform=ax.transAxes, ha="right", va="top",
                fontfamily="monospace", fontsize=11, color=P1C, zorder=9)
        ax.text(tx, ty, "\n\n" + r2, transform=ax.transAxes, ha="right", va="top",
                fontfamily="monospace", fontsize=11, color=P2C, zorder=9)

        # (A) research markers on BOTH players (P1 solid/filled, P2 dashed/hollow).
        add_marks(curve.get("eco", []), grid[:keep], vm[:keep], xs[-1], GREEN, "#e9f6ee", False)
        add_marks(curve.get("mil_upg", []), grid[:keep], mm[:keep], xs[-1], RED, "#fbeaea", False)
        add_marks(curve2.get("eco", []), curve2["grid"][:keep2], vm2[:keep2], xs2[-1], GREEN, "#e9f6ee", True)
        add_marks(curve2.get("mil_upg", []), curve2["grid"][:keep2], mm2[:keep2], xs2[-1], RED, "#fbeaea", True)
    else:
        age_levels = (ymax * 0.985, ymax * 0.93)   # alternate height so close ages don't clash
        for idx, (t, lab) in enumerate(zip(curve["ages"], ("Feudal", "Castle", "Imperial"))):
            if t and t / 60 <= xs[-1]:
                ax.axvline(t / 60, color="#666666", ls="--", lw=1.5, zorder=2)
                age_texts.append(ax.text(
                    t / 60, age_levels[idx % 2], f"{lab}  {_secs(t)}", ha="center", va="top",
                    fontsize=12, fontweight="bold", color="#444444", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#bdbdbd", alpha=0.95)))
        add_marks(curve.get("eco", []), grid[:keep], vm[:keep], xs[-1], GREEN, "#e9f6ee", False)
        add_marks(curve.get("mil_upg", []), grid[:keep], mm[:keep], xs[-1], RED, "#fbeaea", False)

    ax.set_ylim(0, ymax)
    ax.set_xlim(0, max(xs[-1], xs2[-1]) if compare else xs[-1])
    ax.set_xlabel("Game time (minutes)", fontsize=15, fontweight="bold")
    ax.set_ylabel("Average cumulative count  (villagers / military)", fontsize=15, fontweight="bold")
    ax.grid(True, ls=":", alpha=0.4)

    ax2 = ax.twinx()
    ax2.plot(xs, n[:keep], color=GREY, ls=":", lw=2, zorder=2,
             label=None if compare else "Games contributing (n)")
    nmax = curve["n"]
    if compare:
        ax2.plot(xs2, n2[:keep2], color=GREY, ls=DOT, lw=1.6, zorder=2)
        nmax = max(nmax, curve2["n"])
    ax2.set_ylim(0, nmax * 1.12)
    ax2.set_ylabel("Games contributing (n)", fontsize=13, color=GREY)
    ax2.tick_params(axis="y", colors=GREY)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=13, loc="center left", framealpha=0.95)
    if compare:
        title = (f"{_short(name)}  vs  {_short(name2)} — production timeline  ·  last {days} days\n"
                 f"green = villagers · red = military   |   solid = {_short(name)} · "
                 f"dotted = {_short(name2)}   |   age-ups coloured per player   ·   "
                 f"{curve['n']} vs {curve2['n']} games")
    else:
        title = (f"{name} — production timeline  ·  last {days} days  ·  {curve['n']} games\n"
                 "green = villagers + economy upgrades   |   red = military + attack/armour upgrades   "
                 "|   shaded = 95% CI   ·   dotted = games still in progress")
    fig.suptitle(title, fontsize=18, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    if upgrades or age_texts:               # de-collide research labels against age labels + table
        _decollide(fig, upgrades, age_texts)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    buf.seek(0)
    return buf
