"""Unit tests for nammaoe2bot/ingest/persona.py — persona derivation contract."""
from nammaoe2bot.ingest.persona import derive_persona


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


def test_aggressive_carry_is_army_printer():
	# centurion12-shaped: pool-high army, half the games as team-top impact.
	p = derive_persona(_stats(avg_army=54.2, avg_recovery=59.7, carry_rate=51,
	                          tag_rates={"Map pressure": 15, "High impact": 26}))
	assert p["name"] == "Army Printer"
	assert p["role"] == "carry"
	assert p["epithet"] == "Board Topper"


def test_no_name_or_tagline_claims_kills_or_wins():
	"""The parser records units created, never kills; impact carries no win
	term. Nothing player-facing may imply either."""
	from nammaoe2bot.ingest.persona import ROLES, STYLES
	banned = ("villager menace", "kill", "raid", "carries", "hard-carry",
	          "hard-carries", "donates", "elo", "wins", "won", "victory")
	blobs = [f"{v['name']} {v['tagline']}" for v in STYLES.values()]
	blobs += [f"{v['name']} {v['read']}" for v in ROLES.values()]
	for blob in blobs:
		low = blob.lower()
		assert not any(b in low for b in banned), f"outcome/kill claim in: {blob}"


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
	assert p["name"] == "Age-Up Speedrunner"
	assert p["role"] == "engine"


def test_payload_tag_labels_also_count():
	# Web payloads say "Timing edge"/"Recovery" instead of the stored names.
	p = derive_persona(_stats(avg_timing=54, tag_rates={"Timing edge": 20}))
	assert p["name"] == "Age-Up Speedrunner"


def test_reboom_profile_is_late_bloomer():
	p = derive_persona(_stats(avg_recovery=57.2, avg_timing=43.3, carry_rate=9,
	                          tag_rates={"Reboom": 10}))
	assert p["name"] == "Late Bloomer"
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


class TestStyleMargin:
	"""A style must win decisively — a near-tie is not a personality."""

	def test_near_tie_between_two_styles_falls_back_to_flex(self):
		# Mr_PrIMeZ-shaped: phoenix 8.0 vs aggressor 7.9 under the old bare max().
		p = derive_persona(_stats(avg_army=53.6, avg_recovery=60.7))
		assert p["style"] == "flex"
		assert p["name"] == "Certified Flex"

	def test_clear_winner_still_names_the_style(self):
		p = derive_persona(_stats(avg_eco=59.4, tag_rates={"Boom carry": 16}))
		assert p["style"] == "boomer"

	def test_near_tie_with_slow_ages_prefers_slow_cooker_over_flex(self):
		p = derive_persona(_stats(avg_army=53.6, avg_recovery=60.7, avg_timing=44))
		assert p["style"] == "slowcooker"

	def test_margin_is_measured_against_the_runner_up_not_the_baseline(self):
		# Both axes far above baseline, but only 1.0 apart -> still no winner.
		strong_tie = derive_persona(_stats(avg_army=56.0, avg_eco=59.6))
		assert strong_tie["style"] == "flex"


class TestRoleVolatilityThresholds:
	"""Recalibrated to the replay pool: sd p25=5.6, p50=6.0, p75=6.3."""

	def test_pool_median_volatility_is_not_a_wildcard(self):
		# sd 6.0 was above the old 5.9 cut, so a median player read as a coinflip.
		p = derive_persona(_stats(impact_sd=6.0, carry_rate=20))
		assert p["role"] != "wildcard"

	def test_genuinely_swingy_is_still_a_wildcard(self):
		assert derive_persona(_stats(impact_sd=6.8, carry_rate=20))["role"] == "wildcard"

	def test_steady_but_rarely_top_is_support_not_engine(self):
		# The pool's steadiest players sit at 0-10% carry; they are Squad Glue.
		p = derive_persona(_stats(impact_sd=4.3, carry_rate=9))
		assert p["role"] == "support"

	def test_engine_is_reachable_at_the_carry_rate_steady_players_actually_have(self):
		# TiShi-shaped: 16% carry, sd 5.49. The old 22% floor sat above every
		# steady player in the pool, so this branch could never be taken.
		p = derive_persona(_stats(impact_sd=5.49, carry_rate=16))
		assert p["role"] == "engine"
		assert p["epithet"] == "Diesel Engine"

	def test_engine_floor_does_not_poach_from_support(self):
		# Just under the floor with the same steadiness -> still Squad Glue.
		p = derive_persona(_stats(impact_sd=5.49, carry_rate=12))
		assert p["role"] == "support"
