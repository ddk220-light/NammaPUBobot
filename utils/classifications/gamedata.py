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


def _is_militia_line(e):
    """Militia line (incl. Man-at-Arms upgrades) plus the Sicilian Serjeant; excludes the
    imperial Burgundian 'Flemish Militia' (not a feudal rush unit). Spearmen are a separate
    category and never match. Serjeant is bucketed as unique_other, so it is matched by name."""
    name = e.get("name") or ""
    if name == "Serjeant":
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
