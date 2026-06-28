import asyncio

from bot import civ_sync


class FakeDB:
	def __init__(self):
		self.rows = []

	async def fetchone(self, *_args, **_kwargs):
		return None

	async def insert_many(self, _table, rows, **_kwargs):
		self.rows.extend(rows)


def test_record_lobby_match_matches_profiles_and_writes_civs():
	fake = FakeDB()
	parsed = {
		"aoe2_match_id": 12345,
		"teams": [
			{"is_winner": True, "players": [
				{"aoe2_name": "Alpha", "profile_id": 100, "civ": "Franks"},
				{"aoe2_name": "Gamma", "profile_id": 300, "civ": "Teutons"},
				{"aoe2_name": "Delta", "profile_id": 400, "civ": "Huns"},
				{"aoe2_name": "Epsilon", "profile_id": 500, "civ": "Vikings"},
			]},
			{"is_winner": False, "players": [
				{"aoe2_name": "Beta", "profile_id": 200, "civ": "Mayans"},
				{"aoe2_name": "Zeta", "profile_id": 600, "civ": "Mongols"},
				{"aoe2_name": "Eta", "profile_id": 700, "civ": "Britons"},
				{"aoe2_name": "Theta", "profile_id": 800, "civ": "Chinese"},
			]},
		],
	}
	players = [(1, "A", 0), (2, "B", 1), (3, "C", 0), (4, "D", 0), (5, "E", 0), (6, "F", 1), (7, "G", 1), (8, "H", 1)]
	ok = asyncio.run(civ_sync.record_lobby_match(
		9, 77,
		players,
		winner=0,
		match_at=999,
		parsed=parsed,
		db_adapter=fake,
		uid_to_pids={1: [100], 2: [200], 3: [300], 4: [400], 5: [500], 6: [600], 7: [700], 8: [800]},
		nick_to_pids={},
	))

	assert ok is True
	assert len(fake.rows) == 8
	assert fake.rows[:2] == [
		{
			"channel_id": 9,
			"aoe2_match_id": 12345,
			"aoe2_name": "Alpha",
			"civ": "Franks",
			"at": 999,
			"bot_match_id": 77,
			"user_id": 1,
			"nick": "A",
			"team": 0,
			"result": "W",
		},
		{
			"channel_id": 9,
			"aoe2_match_id": 12345,
			"aoe2_name": "Beta",
			"civ": "Mayans",
			"at": 999,
			"bot_match_id": 77,
			"user_id": 2,
			"nick": "B",
			"team": 1,
			"result": "L",
		},
	]


def test_record_lobby_match_rejects_low_overlap():
	fake = FakeDB()
	parsed = {
		"aoe2_match_id": 12345,
		"teams": [
			{"is_winner": True, "players": [
				{"aoe2_name": "P1", "profile_id": 100, "civ": "Franks"},
				{"aoe2_name": "P2", "profile_id": 200, "civ": "Mayans"},
				{"aoe2_name": "P3", "profile_id": 300, "civ": "Teutons"},
				{"aoe2_name": "P4", "profile_id": 400, "civ": "Huns"},
			]},
			{"is_winner": False, "players": [
				{"aoe2_name": "P5", "profile_id": 500, "civ": "Vikings"},
				{"aoe2_name": "P6", "profile_id": 600, "civ": "Mongols"},
				{"aoe2_name": "P7", "profile_id": 700, "civ": "Britons"},
			]},
		],
	}
	ok = asyncio.run(civ_sync.record_lobby_match(
		9, 77,
		[(1, "A", 0), (2, "B", 1), (3, "C", 1), (4, "D", 0), (5, "E", 0), (6, "F", 1), (7, "G", 1), (8, "H", 0)],
		winner=0,
		match_at=999,
		parsed=parsed,
		db_adapter=fake,
		uid_to_pids={1: [100], 2: [200], 3: [300], 4: [400], 5: [500], 6: [600], 7: [700], 8: [800]},
		nick_to_pids={},
	))

	assert ok is False
	assert fake.rows == []
