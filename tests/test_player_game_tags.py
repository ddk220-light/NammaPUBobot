from bot.replay_stats.player_tags import derive_tags


def _player(**kw):
	base = {
		"player_number": 1,
		"villagers": 90,
		"vil_pre_castle": 22,
		"military": 80,
		"mil_pre_castle": 3,
		"feudal_s": 720,
		"castle_s": 1200,
		"imperial_s": 2400,
	}
	base.update(kw)
	return base


def test_derives_naked_fc_and_greedy_boom():
	row = _player(villagers=130, vil_pre_castle=33, military=75, mil_pre_castle=2, castle_s=1050)
	group = [
		row,
		_player(player_number=2, villagers=90, vil_pre_castle=22, military=80, mil_pre_castle=7, castle_s=1250),
		_player(player_number=3, villagers=85, vil_pre_castle=20, military=95, mil_pre_castle=10, castle_s=1300),
	]
	tags = {t["tag"] for t in derive_tags(row, group)}
	assert "Naked FC" in tags
	assert "Greedy boom" in tags
	# Boom-first eco lead with lean early army is the Boom carry profile
	# (pre-recalibration this fixture landed on the generic "Eco carry").
	assert "Boom carry" in tags


def test_every_player_gets_at_least_one_tag():
	# Empty parse (all production zero/NULL) must store an explicit
	# "Partial replay" instead of nothing.
	empty = {"player_number": 1, "villagers": 0, "military": 0}
	group = [empty, {"player_number": 2, "villagers": 0, "military": 0}]
	tags = derive_tags(empty, group)
	assert [t["tag"] for t in tags] == ["Partial replay"]
	assert tags[0]["category"] == "data"

	# A flat mid player with real data gets exactly one coverage tag.
	mid = _player()
	group = [mid, _player(player_number=2), _player(player_number=3)]
	tags = derive_tags(mid, group)
	assert len(tags) >= 1


def test_derives_composition_and_upgrade_tags():
	row = _player(villagers=95, vil_pre_castle=24, military=160, mil_pre_castle=16, castle_s=1350)
	group = [row, _player(player_number=2), _player(player_number=3)]
	units = [
		{"category": "knight_line", "is_military": 1, "total": 80},
		{"category": "siege", "is_military": 1, "total": 9},
	]
	techs = [
		{"tech": "Bloodlines", "click_s": 1600},
		{"tech": "Scale Barding Armor", "click_s": 1700},
	]
	tags = {t["tag"] for t in derive_tags(row, group, units, techs)}
	assert "Feudal all-in" in tags
	assert "Knight-heavy comp" in tags
	assert "Upgrade timer" in tags
