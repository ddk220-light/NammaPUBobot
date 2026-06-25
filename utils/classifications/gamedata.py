"""Pure read accessors over an extract_match() output dict. No DB, no mgz."""


def player(game, pnum):
    for p in game.get("players", []):
        if p["player_number"] == pnum:
            return p
    return None


def player_numbers(game):
    return [p["player_number"] for p in game.get("players", [])]


def archer_queue_events(game, pnum):
    """Foot-archer-line queue events for a player, timestamped, sorted by time.
    Excludes skirmishers/cav-archers (separate categories) and null-timestamp queues."""
    evs = [e for e in game.get("events", [])
           if e["player_number"] == pnum
           and e.get("category") == "archer_line"
           and e.get("t_s") is not None]
    return sorted(evs, key=lambda e: e["t_s"])


# Cavalry scout line, matched by name. The extractor's "scout" category also catches the Meso
# *infantry* Eagle Scout and the modded New-World "Champi Scout" (Tupi/Incas/Mapuche/Muisca),
# both of which are NOT a scout-cavalry rush -- so we allowlist the real cav scouts by name.
SCOUT_CAV_NAMES = ("scout cavalry", "camel scout")


def scout_queue_events(game, pnum):
    """Cavalry-scout queue events for a player, timestamped, sorted by time.
    Allowlists Scout Cavalry / Camel Scout by name (excludes Eagle Scout and Champi Scout)."""
    evs = [e for e in game.get("events", [])
           if e["player_number"] == pnum
           and (e.get("name") or "").lower() in SCOUT_CAV_NAMES
           and e.get("t_s") is not None]
    return sorted(evs, key=lambda e: e["t_s"])


# Militia-line equivalents the extractor buckets elsewhere, matched by name:
#  - Serjeant: Sicilian unique infantry (bucketed unique_other)
#  - Champi Scout: the militia/club-infantry unit of the modded New-World civs
#    (Tupi/Incas/Mapuche/Muisca); a "champi" is an Andean war club, so it is infantry despite
#    the "Scout" name -- and is excluded from the scout-cavalry allowlist above.
MILITIA_LINE_BY_NAME = ("Serjeant", "Champi Scout")


def _is_militia_line(e):
    """Militia line (incl. Man-at-Arms upgrades) plus militia-equivalent infantry matched by
    name (see MILITIA_LINE_BY_NAME); excludes the imperial Burgundian 'Flemish Militia' (not a
    feudal rush unit). Spearmen are a separate category and never match."""
    name = e.get("name") or ""
    if name in MILITIA_LINE_BY_NAME:
        return True
    return e.get("category") == "militia_line" and name != "Flemish Militia"


def militia_queue_events(game, pnum):
    """Militia-line + Serjeant queue events for a player, timestamped, sorted by time."""
    evs = [e for e in game.get("events", [])
           if e["player_number"] == pnum
           and _is_militia_line(e)
           and e.get("t_s") is not None]
    return sorted(evs, key=lambda e: e["t_s"])


def tech_click_s(game, pnum, tech):
    for t in game.get("techs", []):
        if t["player_number"] == pnum and t.get("tech") == tech:
            return t.get("click_s")
    return None


# --- Early-Castle window (the "Early Castle Builds" rush family) ---------------------------------
# The window is [Castle-age click, build of the 3rd ADDITIONAL Town Center) -- i.e. castle-age
# aggression before the player commits to a heavy (4-TC) boom. A 1st and 2nd additional TC are
# allowed INSIDE the window. tc_build_s (sorted TC build timestamps) is emitted by extract.py from
# v2 on; older caches won't have it (treated as "never boomed").

def early_castle_window(game, pnum):
    """(start, end) seconds. start = Castle-age click; end = the 3rd ADDITIONAL TC's build time
    (None = never built a 3rd extra TC, so the window stays open; a 1st/2nd extra TC is allowed).
    (None, None) if the player never clicked Castle. "Additional" TCs = those built at/after the
    Feudal click, which excludes a Nomad-map starting TC (Dark Age) so the rule holds on every map."""
    p = player(game, pnum)
    if not p or p.get("castle_s") is None:
        return (None, None)
    feudal_s = p.get("feudal_s") or 0
    extra = [t for t in (p.get("tc_build_s") or []) if t >= feudal_s]
    return (p["castle_s"], extra[2] if len(extra) >= 3 else None)


def late_castle_window(game, pnum):
    """[3rd additional TC build, Imperial click) -- the late-castle army phase, which begins exactly
    where early_castle_window ends. (None, None) if the player never built a 3rd additional TC (no
    late phase). end = imperial_s, or None (open to game end) if Imperial was never clicked.
    "Additional" TCs = built at/after the Feudal click (excludes a Nomad Dark-Age starting TC)."""
    p = player(game, pnum)
    if not p:
        return (None, None)
    feudal_s = p.get("feudal_s") or 0
    extra = [t for t in (p.get("tc_build_s") or []) if t >= feudal_s]
    if len(extra) < 3:
        return (None, None)
    return (extra[2], p.get("imperial_s"))


def _in_window(t, start, end):
    return t is not None and start is not None and t >= start and (end is None or t < end)


def queued_in_window(game, pnum, pred, start, end):
    """Sum of queued amounts for production events matching pred(event) within [start, end)."""
    return sum((e.get("amount") or 1)
               for e in game.get("events", [])
               if e["player_number"] == pnum and _in_window(e.get("t_s"), start, end) and pred(e))


def tech_in_window(game, pnum, tech, start, end):
    """(1.0 if `tech` was clicked within [start, end) else 0.0, click_s or None)."""
    click = tech_click_s(game, pnum, tech)
    return (1.0 if _in_window(click, start, end) else 0.0, float(click) if click is not None else None)


# --- Castle placement: forward vs safe (needs extract v3 positions) ------------------------------

def _xy(d):
    return (d["x"], d["y"]) if d else None


def _dist(a, b):
    if a is None or b is None:
        return None
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def home_tc_xy(game, pnum):
    """The player's home Town Center position = the LAST TC they place before the Castle click
    (so a delete->replace lands on the surviving replacement), falling back to the pre-placed
    starting TC. None if neither is known."""
    p = player(game, pnum)
    if not p:
        return None
    castle_s = p.get("castle_s")
    pre = [b for b in (p.get("tc_builds") or [])
           if b.get("t_s") is not None and (castle_s is None or b["t_s"] < castle_s)]
    if pre:
        return _xy(max(pre, key=lambda b: b["t_s"]))
    return _xy(p.get("start_tc_xy"))


def primary_castle(game, pnum):
    """The player's primary Castle: the first Castle built in Castle Age BEFORE any additional TC
    of the Castle Age (i.e. a castle drop, not a boom). Returns {x,y,t_s} or None."""
    p = player(game, pnum)
    if not p:
        return None
    castle_s = p.get("castle_s")
    if castle_s is None:
        return None
    castle_age_tc = min((t for t in (p.get("tc_build_s") or []) if t >= castle_s), default=None)
    end = castle_age_tc if castle_age_tc is not None else float("inf")
    cands = [c for c in (p.get("castle_builds") or [])
             if c.get("t_s") is not None and castle_s <= c["t_s"] < end]
    return min(cands, key=lambda c: c["t_s"]) if cands else None


def castle_placement(game, pnum):
    """For a primary castle, return (is_forward, dist_own, dist_enemy) or None if it can't be
    judged (no primary castle, or missing own/enemy home TC). Forward = the castle is closer to
    the nearest opponent's home TC than to the player's own home TC. Opponents = different team."""
    castle = primary_castle(game, pnum)
    own = home_tc_xy(game, pnum)
    if not castle or own is None:
        return None
    cxy = (castle["x"], castle["y"])
    me = player(game, pnum)
    my_team = me.get("team")
    enemy_dists = []
    for op in game.get("players", []):
        if op["player_number"] == pnum or op.get("team") == my_team:
            continue
        d = _dist(cxy, home_tc_xy(game, op["player_number"]))
        if d is not None:
            enemy_dists.append(d)
    if not enemy_dists:
        return None
    dist_own = _dist(cxy, own)
    dist_enemy = min(enemy_dists)
    return (dist_enemy < dist_own, dist_own, dist_enemy)
