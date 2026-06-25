"""Turn a parsed game (extract_match output) into rows for the local DB. Reuses the registry +
shape exactly as the runner does, so local and prod classification logic stay identical."""
from utils.classifications import shape
from utils.classifications.registry import REGISTRY


def _winner_int(w):
    return 1 if w in (1, True) else 0 if w in (0, False) else None


def classify_game(game, mid, played_at):
    """-> (result_rows, metric_rows, player_rows). player_rows = (mid, pnum, identity, winner) for
    EVERY player-game (the cls_player_totals source); result/metric rows only for matched triggers."""
    result_rows, metric_rows = [], []
    player_rows = []
    for p in game.get("players", []):
        pnum = p["player_number"]
        player_rows.append((int(mid), pnum, p.get("identity") or "?", _winner_int(p.get("winner"))))
    for key, c in REGISTRY.items():
        for p in game.get("players", []):
            pnum = p["player_number"]
            if not c.trigger(game, pnum):
                continue
            result_rows.append(shape.result_row(key, mid, p, played_at))
            metric_rows.extend(shape.metric_rows(key, mid, pnum, c.factors(game, pnum)))
    return result_rows, metric_rows, player_rows
