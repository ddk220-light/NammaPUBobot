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


def tech_click_s(game, pnum, tech):
    for t in game.get("techs", []):
        if t["player_number"] == pnum and t.get("tech") == tech:
            return t.get("click_s")
    return None
