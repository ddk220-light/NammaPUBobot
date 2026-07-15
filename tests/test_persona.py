"""Unit tests for bot/replay_stats/persona.py — persona derivation contract."""
from bot.replay_stats.persona import derive_persona


def _stats(**kw):
	base = {
		"matches": 100,
		"avg_army": 50, "avg_eco": 50, "avg_timing": 50, "avg_recovery": 50,
		"impact_sd": 5.3, "carry_rate": 25, "tag_rates": {},
	}
	base.update(kw)
	return base


def test_unscouted_below_min_games():
	p = derive_persona(_stats(matches=5))
	assert p["key"] == "unscouted"
	assert p["role"] is None
	assert "5 parsed replays" in p["evidence"][0]


def test_aggressive_carry_is_villager_menace():
	# centurion12-shaped: pool-high army, half the games as team-top impact.
	p = derive_persona(_stats(avg_army=54.2, avg_recovery=59.7, carry_rate=51,
	                          tag_rates={"Map pressure": 15, "High impact": 26}))
	assert p["name"] == "Villager Menace"
	assert p["role"] == "carry"
	assert p["epithet"] == "Designated Carry"


def test_eco_heavy_is_farm_enjoyer():
	# bloodless.-shaped: eco 59, eco/boom tags, 40% carry.
	p = derive_persona(_stats(avg_eco=59.4, carry_rate=40, impact_sd=6.0,
	                          tag_rates={"Eco carry": 18, "Boom carry": 16}))
	assert p["name"] == "Farm Enjoyer"
	assert p["role"] == "carry"


def test_fast_ager_low_variance_is_speedrunner_engine():
	# M1k3-shaped: timing 57, 27% tempo tags, steady output.
	p = derive_persona(_stats(avg_timing=57.2, avg_recovery=59.7, carry_rate=30,
	                          impact_sd=4.3, tag_rates={"Age-up tempo": 27}))
	assert p["name"] == "Imp Speedrunner"
	assert p["role"] == "engine"


def test_payload_tag_labels_also_count():
	# Web payloads say "Timing edge"/"Recovery" instead of the stored names.
	p = derive_persona(_stats(avg_timing=54, tag_rates={"Timing edge": 20}))
	assert p["name"] == "Imp Speedrunner"


def test_reboom_profile_is_comeback_merchant():
	p = derive_persona(_stats(avg_recovery=57.2, avg_timing=43.3, carry_rate=9,
	                          tag_rates={"Reboom": 10}))
	assert p["name"] == "Comeback Merchant"
	assert p["role"] == "support"


def test_slow_low_signal_is_slow_cooker():
	# sundar7238-shaped: nothing dominant, very slow age-ups, rarely team-top.
	p = derive_persona(_stats(avg_army=47.8, avg_eco=48.3, avg_timing=40.2,
	                          avg_recovery=41.5, carry_rate=5))
	assert p["name"] == "Slow Cooker"
	assert p["role"] == "support"


def test_no_signal_is_certified_flex():
	p = derive_persona(_stats(avg_army=49, avg_eco=49, avg_timing=49, avg_recovery=49))
	assert p["name"] == "Certified Flex"


def test_high_variance_is_coinflip_enjoyer():
	p = derive_persona(_stats(avg_timing=54.2, impact_sd=7.2, carry_rate=32,
	                          tag_rates={"Age-up tempo": 24}))
	assert p["role"] == "wildcard"
	assert p["epithet"] == "Coinflip Enjoyer"


def test_missing_fields_do_not_crash():
	p = derive_persona({"matches": 50})
	assert p["name"]
	assert p["role"] == "anchor"
	p2 = derive_persona(None)
	assert p2["key"] == "unscouted"
