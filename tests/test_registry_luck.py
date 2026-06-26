from utils.classifications.registry import REGISTRY

LUCK_KEYS = {
    "spawn_near_enemy", "spawn_near_ally", "spawn_isolated", "spawn_near_gold", "spawn_gold_poor",
    "spawn_near_stone", "spawn_stone_poor", "spawn_near_food", "spawn_food_poor",
    "tight_villagers", "scattered_villagers", "luck_baseline",
}


def test_all_luck_keys_registered_with_category_luck():
    assert LUCK_KEYS <= set(REGISTRY)
    for k in LUCK_KEYS:
        assert REGISTRY[k].category == "luck"


def test_strategy_keys_untouched():
    assert REGISTRY["knight_rush"].category == "strategy"
    assert REGISTRY["boom_to_imp"].category == "strategy"
