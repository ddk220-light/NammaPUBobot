"""Pure transforms: a matched player + its factors dict -> cls_results / cls_result_metrics
row dicts. No DB. None-valued metrics are dropped (a missing row = the factor didn't apply)."""


def result_row(key, aoe2_match_id, player, played_at):
    winner = player.get("winner")
    team = player.get("team")
    return {
        "key": key,
        "aoe2_match_id": aoe2_match_id,
        "player_number": player["player_number"],
        "profile_id": player.get("profile_id"),
        "identity": player.get("identity"),
        "civ": player.get("civ"),
        "team": str(team) if team is not None else None,
        "winner": None if winner is None else (1 if winner else 0),
        "played_at": played_at,
    }


def metric_rows(key, aoe2_match_id, player_number, factors):
    rows = []
    for metric, value in factors.items():
        if value is None:
            continue
        rows.append({"key": key, "aoe2_match_id": aoe2_match_id,
                     "player_number": player_number, "metric": metric, "value": float(value)})
    return rows
