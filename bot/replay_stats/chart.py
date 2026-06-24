# -*- coding: utf-8 -*-
"""matplotlib renderer for the /player_details build-timeline chart. Headless (Agg) + the OO
Figure API (no pyplot global state, safe under the async bot) — mirrors bot/player_profile.py.
matplotlib is imported lazily inside render_timeline so importing this module stays cheap and
test-safe (CI has no matplotlib)."""
import io


def _secs(v):
    return f"{int(v) // 60}:{int(v) % 60:02d}"


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
