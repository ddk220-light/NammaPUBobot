"""The 'luck' family: map/spawn factors (settle proximity, nearby gold/stone/food, villager spread)
and their win/loss impact. Each use case is a non-exclusive Classification (category 'luck') that
only fires on a valid Nomad 6/8p game (gd.is_valid_luck_game). Thresholds are absolute tiles, frozen
from the 2026-06-25 calibration (~20% of player-games per bucket; isolated 25%). A luck_baseline
cohort fires for every player in a valid game -> the denominator (N) and the 50% reference."""
from utils.classifications import gamedata as gd
from utils.classifications.contract import Classification, req

# metric(game, pnum) -> float|None
_PROX = lambda i: (lambda g, p: gd.spawn_proximity(g, p)[i])   # 0 ally, 1 enemy, 2 any  # noqa: E731
_FIELD = lambda k: (lambda g, p: gd.spawn_metric(g, p, k))     # noqa: E731

_REQ = [
    req("settle_tc_xy", source="extract.players.settle_tc_xy", status="available",
        note="first built TC (Nomad has no pre-placed TC); extract v4"),
    req("spawn_resources", source="extract.players.spawn_gold_d/stone_d/food_d", status="available",
        note="distance from settle to nearest gaia gold/stone/huntable; extract v4"),
    req("vil_perim", source="extract.players.vil_perim", status="available",
        note="starting-villager triangle perimeter; extract v4"),
    req("winner", source="extract.players.winner", status="available",
        note="outcome dimension; consumed by the runner, not trigger/factors"),
]


def _make(key, title, metric, side, threshold, label, spec):
    def trigger(game, pnum):
        if not gd.is_valid_luck_game(game):
            return False
        v = metric(game, pnum)
        if v is None:
            return False
        return v < threshold if side == "near" else v > threshold

    def factors(game, pnum):
        v = metric(game, pnum)
        return {label: round(v, 1) if v is not None else None}

    return Classification(
        key=key, title=title, version=1, trigger_spec=spec, category="luck",
        requirements=_REQ, trigger=trigger, factors=factors,
        factor_specs=[dict(metric=label, label=label.replace("_", " ").title(), kind="count")])


# (key, title, metric, side, threshold, factor-label, human trigger_spec)
_SPECS = [
    ("spawn_near_enemy", "Spawn near enemy", _PROX(1), "near", 36, "enemy_dist",
     "settled within 36 tiles of the nearest enemy's first TC"),
    ("spawn_near_ally", "Spawn near ally", _PROX(0), "near", 46, "ally_dist",
     "settled within 46 tiles of the nearest ally's first TC"),
    ("spawn_isolated", "Spawn isolated", _PROX(2), "far", 63, "nearest_player_dist",
     "settled more than 63 tiles from every other player's first TC"),
    ("spawn_near_gold", "Spawn near gold", _FIELD("spawn_gold_d"), "near", 7, "gold_dist",
     "settled within 7 tiles of gold"),
    ("spawn_gold_poor", "Spawn gold-poor", _FIELD("spawn_gold_d"), "far", 17, "gold_dist",
     "nearest gold more than 17 tiles from the settle"),
    ("spawn_near_stone", "Spawn near stone", _FIELD("spawn_stone_d"), "near", 9, "stone_dist",
     "settled within 9 tiles of stone"),
    ("spawn_stone_poor", "Spawn stone-poor", _FIELD("spawn_stone_d"), "far", 20, "stone_dist",
     "nearest stone more than 20 tiles from the settle"),
    ("spawn_near_food", "Spawn near food", _FIELD("spawn_food_d"), "near", 7, "food_dist",
     "settled within 7 tiles of huntable food (boar/deer)"),
    ("spawn_food_poor", "Spawn food-poor", _FIELD("spawn_food_d"), "far", 15, "food_dist",
     "nearest huntable food more than 15 tiles from the settle"),
    ("tight_villagers", "Tight villager spawn", _FIELD("vil_perim"), "near", 253, "vil_perimeter",
     "starting villagers close together (triangle perimeter < 253 tiles)"),
    ("scattered_villagers", "Scattered villager spawn", _FIELD("vil_perim"), "far", 429, "vil_perimeter",
     "starting villagers spread out (triangle perimeter > 429 tiles)"),
]

CLASSIFICATIONS = [_make(*s) for s in _SPECS]


def _baseline():
    def trigger(game, pnum):
        return gd.is_valid_luck_game(game)

    def factors(game, pnum):
        d_ally, d_enemy, d_any = gd.spawn_proximity(game, pnum)
        out = {"ally_dist": d_ally, "enemy_dist": d_enemy, "nearest_player_dist": d_any,
               "gold_dist": gd.spawn_metric(game, pnum, "spawn_gold_d"),
               "stone_dist": gd.spawn_metric(game, pnum, "spawn_stone_d"),
               "food_dist": gd.spawn_metric(game, pnum, "spawn_food_d"),
               "vil_perimeter": gd.spawn_metric(game, pnum, "vil_perim")}
        return {k: (round(v, 1) if v is not None else None) for k, v in out.items()}

    return Classification(
        key="luck_baseline", title="All valid spawns (baseline)", version=1, category="luck",
        trigger_spec="every player in a valid Nomad 6/8p game (balanced winner) — the 50% reference",
        requirements=_REQ, trigger=trigger, factors=factors,
        factor_specs=[dict(metric=m, label=m.replace("_", " ").title(), kind="count")
                      for m in ("ally_dist", "enemy_dist", "nearest_player_dist",
                                "gold_dist", "stone_dist", "food_dist", "vil_perimeter")])


CLASSIFICATIONS.append(_baseline())
