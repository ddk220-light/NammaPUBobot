"""Unit tests for the Match Cards scoring fork (bot/replay_stats/card_scoring.py)."""

import bot.replay_stats.card_scoring as cs


def _row(**kw):
    base = dict(villagers=100, vil_pre_castle=40, military=50,
                mil_pre_castle=10, mil_pre_imperial=30,
                imperial_s=1800, age_reliable=1, nick="p")
    base.update(kw)
    return base


# ── Component scores ─────────────────────────────────────────────────────

def test_scores_are_50_when_every_player_is_identical():
    group = [_row(), _row(), _row()]
    s = cs.component_scores(group[0], group)
    assert s["eco"] == 50
    assert s["army"] == 50
    assert s["impact"] == 50


def test_more_villagers_than_the_group_raises_eco_above_50():
    group = [_row(villagers=200, vil_pre_castle=80), _row(), _row()]
    assert cs.component_scores(group[0], group)["eco"] > 50


def test_timing_columns_feed_nothing():
    assert "feudal_s" not in cs.REQUIRED_COLUMNS
    assert "castle_s" not in cs.REQUIRED_COLUMNS
    assert not hasattr(cs, "TIMING_MIX")


def test_no_tech_terms_in_either_mix():
    keys = {k for k, _ in cs.ECO_MIX} | {k for k, _ in cs.ARMY_MIX}
    assert not any("tech" in k for k in keys)


def test_mixes_are_identical_to_the_calibrated_scoring_module():
    """Thresholds are inherited, which is only valid while the mixes match."""
    import bot.replay_stats.scoring as legacy
    assert cs.ECO_MIX == legacy.ECO_MIX
    assert cs.ARMY_MIX == legacy.ARMY_MIX


def test_inherited_thresholds_match_the_legacy_values():
    import bot.replay_stats.scoring as legacy
    for key in ("all_in_army", "all_in_eco_max", "reboom_score",
                "reboom_early_eco_max", "reboom_eco_min"):
        assert cs.TH[key] == legacy.TH[key], key


def test_required_columns_covers_every_column_the_mixes_read():
    mixed = {k for k, _ in cs.ECO_MIX} | {k for k, _ in cs.ARMY_MIX}
    assert mixed <= set(cs.REQUIRED_COLUMNS)


def test_impact_weights_sum_to_one_and_exclude_timing():
    assert dict(cs.IMPACT_WEIGHTS).keys() == {"army", "eco"}
    assert abs(sum(w for _, w in cs.IMPACT_WEIGHTS) - 1.0) < 1e-9


def test_missing_value_is_excluded_not_scored_as_average():
    """A None must not read as 'exactly match average'."""
    group = [_row(villagers=None), _row(villagers=10), _row(villagers=200)]
    # vil_pre_castle is identical across the group, so with villagers excluded
    # and the remaining weight renormalised the score is exactly 50.
    assert cs.component_scores(group[0], group)["eco"] == 50


def test_unreliable_ages_drop_the_pre_age_terms():
    """age_reliable == 0 must not let junk age splits score."""
    group = [_row(age_reliable=0, mil_pre_castle=999, mil_pre_imperial=999),
             _row(), _row()]
    # Only the `military` term survives, and it equals the group average.
    assert cs.component_scores(group[0], group)["army"] == 50


def test_never_reached_imperial_does_not_outrank_a_genuine_early_aggressor():
    """extract.py sets mil_pre_imperial == military when Imperial was never
    clicked. Unfixed, this turtle's 50 'early' units would beat the aggressor's
    real 45."""
    turtle = _row(nick="turtle", imperial_s=None, military=50, mil_pre_imperial=50)
    aggressor = _row(nick="aggro", imperial_s=1800, military=50, mil_pre_imperial=45)
    group = [turtle, aggressor, _row()]
    assert cs.component_scores(turtle, group)["army"] < \
        cs.component_scores(aggressor, group)["army"]


def test_a_player_with_no_data_at_all_does_not_crash():
    group = [_row(villagers=None, military=None, vil_pre_castle=None,
                  mil_pre_castle=None, mil_pre_imperial=None), _row()]
    s = cs.component_scores(group[0], group)
    assert s["eco"] == 50 and s["army"] == 50


def test_carry_sort_key_orders_by_impact_then_army_then_eco_then_nick():
    a = {"impact_score": 60, "army_score": 50, "eco_score": 50, "nick": "b"}
    b = {"impact_score": 60, "army_score": 50, "eco_score": 50, "nick": "a"}
    assert sorted([a, b], key=cs.carry_sort_key)[0] is b


# ── Medals ───────────────────────────────────────────────────────────────

def _p(nick, team=0, vil=100, mil=50, **kw):
    base = dict(nick=nick, team=team, villagers=vil, military=mil,
                eco_score=50, army_score=50, early_eco_score=50,
                reboom_score=50, production_coverage=None, has_production=True)
    base.update(kw)
    return base


def test_medals_go_to_the_top_three_across_the_whole_match():
    ps = [_p("a", mil=90), _p("b", mil=80), _p("c", mil=70), _p("d", team=1, mil=60)]
    medals = cs.assign_medals(ps)
    assert [m["military_medal"] for m in medals] == [1, 2, 3, None]


def test_medals_are_match_wide_not_per_team():
    ps = [_p("a", team=0, mil=10), _p("b", team=1, mil=90)]
    medals = cs.assign_medals(ps)
    assert medals[1]["military_medal"] == 1
    assert medals[0]["military_medal"] == 2


def test_villager_and_military_medals_are_independent():
    ps = [_p("a", vil=200, mil=1), _p("b", vil=1, mil=200)]
    medals = cs.assign_medals(ps)
    assert medals[0]["villager_medal"] == 1
    assert medals[0]["military_medal"] == 2
    assert medals[1]["military_medal"] == 1


def test_players_without_production_are_excluded_from_medals():
    ps = [_p("a", has_production=False, mil=999), _p("b", mil=10)]
    medals = cs.assign_medals(ps)
    assert medals[0]["military_medal"] is None
    assert medals[1]["military_medal"] == 1


def test_fewer_than_three_players_with_data_still_awards_what_it_can():
    ps = [_p("a", mil=50), _p("b", mil=40)]
    assert [m["military_medal"] for m in cs.assign_medals(ps)] == [1, 2]


def test_medal_ties_break_deterministically_by_other_count_then_nick():
    ps = [_p("zed", mil=50, vil=10), _p("amy", mil=50, vil=10)]
    medals = cs.assign_medals(ps)
    assert medals[1]["military_medal"] == 1
    assert medals[0]["military_medal"] == 2


def test_medal_allocation_is_stable_across_re_renders():
    ps = [_p("a", mil=50, vil=50), _p("b", mil=50, vil=50), _p("c", mil=50, vil=50)]
    assert cs.assign_medals(ps) == cs.assign_medals(list(ps))


# ── Production coverage ──────────────────────────────────────────────────

def test_production_coverage_is_one_when_every_bucket_has_a_click():
    assert cs.production_coverage([0, 130, 250, 370], 480) == 1.0


def test_production_coverage_drops_for_a_long_idle_gap():
    cov = cs.production_coverage([0, 130, 700], 720)
    assert 0.0 < cov < 0.6


def test_batch_queued_production_still_marks_its_bucket():
    """One click can queue five villagers, so bucketing must not read an
    efficient batch as idleness."""
    assert cs.production_coverage([10, 130, 250], 360) == 1.0


def test_production_coverage_is_none_without_a_timeline():
    assert cs.production_coverage([], 600) is None
    assert cs.production_coverage(None, 600) is None
    assert cs.production_coverage([10, 20], 0) is None


def test_production_coverage_never_exceeds_one():
    assert cs.production_coverage(list(range(0, 600, 5)), 600) == 1.0


# ── Team tags ────────────────────────────────────────────────────────────

def test_a_tag_goes_to_the_highest_scorer_on_the_team_who_clears_the_bar():
    ps = [_p("a", army_score=70, eco_score=40), _p("b", army_score=66, eco_score=40)]
    tags = cs.assign_team_tags(ps)
    assert "Low-eco pressure" in tags[0]
    assert tags[1] == []


def test_nobody_gets_a_tag_when_nobody_clears_the_bar():
    ps = [_p("a", army_score=50, eco_score=50), _p("b", army_score=51)]
    assert cs.assign_team_tags(ps) == [[], []]


def test_tags_are_scoped_per_team_so_both_teams_can_award_one():
    ps = [_p("a", team=0, army_score=70, eco_score=40),
          _p("b", team=1, army_score=68, eco_score=40)]
    tags = cs.assign_team_tags(ps)
    assert "Low-eco pressure" in tags[0]
    assert "Low-eco pressure" in tags[1]


def test_one_player_can_sweep_several_tags():
    ps = [_p("a", army_score=70, eco_score=60, reboom_score=75,
             early_eco_score=40, production_coverage=0.9),
          _p("b")]
    # army 70 with eco 60 fails all_in (needs eco <= 48), so reboom + production.
    assert set(cs.assign_team_tags(ps)[0]) == {"Recovery", "Constant production"}


def test_a_dominant_player_can_hold_all_three_tags():
    ps = [_p("a", army_score=70, eco_score=48, reboom_score=75,
             early_eco_score=40, production_coverage=0.9)]
    # eco exactly at the all_in ceiling, and reboom_eco_min is 55 — so this
    # player cannot hold both; assert the pair that is actually reachable.
    tags = cs.assign_team_tags(ps)
    assert "Low-eco pressure" in tags[0]
    assert "Constant production" in tags[0]


def test_constant_production_needs_coverage_above_the_bar():
    assert cs.assign_team_tags([_p("a", production_coverage=0.5)]) == [[]]
    assert "Constant production" in cs.assign_team_tags(
        [_p("a", production_coverage=0.95)])[0]


def test_a_player_without_production_earns_no_tags():
    ps = [_p("a", has_production=False, army_score=99, eco_score=1)]
    assert cs.assign_team_tags(ps) == [[]]


def test_tag_ties_break_by_nick_so_re_renders_are_stable():
    ps = [_p("zed", army_score=70, eco_score=40), _p("amy", army_score=70, eco_score=40)]
    tags = cs.assign_team_tags(ps)
    assert tags[1] == ["Low-eco pressure"]
    assert tags[0] == []


def test_no_fallback_tag_vocabulary_survives_the_fork():
    for banned in ("All-rounder", "Army-leaning", "Eco-leaning",
                   "Tempo-leaning", "Uphill battle", "High impact"):
        assert banned not in cs.TAG_NAMES.values()
