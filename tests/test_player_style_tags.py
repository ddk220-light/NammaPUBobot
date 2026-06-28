from utils.player_style_tags import style_tag_rows


def test_style_tag_rows_marks_knight_pocket_and_fast_castle():
	game = {
		"match": {"duration_s": 3600},
		"players": [{
			"player_number": 1,
			"profile_id": 7,
			"identity": "Pocket",
			"civ": "Franks",
			"team": 1,
			"winner": True,
			"castle_s": 16 * 60,
			"imperial_s": 39 * 60,
			"vil_pre_castle": 28,
			"mil_pre_castle": 2,
			"military": 70,
		}],
		"units": [{"player_number": 1, "category": "knight_line", "total": 24}],
		"techs": [],
	}
	keys = {r["key"] for r in style_tag_rows(game, 99, 123)}
	assert {"role_fast_castle_pocket", "role_knight_pocket"} <= keys


def test_style_tag_rows_marks_opening_pressure_and_siege():
	game = {
		"match": {"duration_s": 2600},
		"players": [{
			"player_number": 2,
			"profile_id": 8,
			"identity": "Flank",
			"civ": "Mayans",
			"team": 2,
			"winner": False,
			"castle_s": 21 * 60,
			"vil_pre_castle": 20,
			"mil_pre_castle": 11,
			"military": 55,
		}],
		"units": [
			{"player_number": 2, "category": "archer_line", "total": 22},
			{"player_number": 2, "category": "siege", "total": 9},
		],
		"techs": [],
	}
	keys = {r["key"] for r in style_tag_rows(game, 100, 124)}
	assert {"role_opening_pressure", "role_archer_tempo", "role_siege_closer"} <= keys
