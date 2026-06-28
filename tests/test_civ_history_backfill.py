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
			]},
			{"is_winner": False, "players": [
				{"aoe2_name": "Beta", "profile_id": 200, "civ": "Mayans"},
			]},
		],
	}
	ok = asyncio.run(civ_sync.record_lobby_match(
		9, 77,
		[(1, "A", 0), (2, "B", 1)],
		winner=0,
		match_at=999,
		parsed=parsed,
		db_adapter=fake,
		uid_to_pids={1: [100], 2: [200]},
		nick_to_pids={},
	))

	assert ok is True
	assert fake.rows == [
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
		"teams": [{"is_winner": True, "players": [
			{"aoe2_name": "Alpha", "profile_id": 100, "civ": "Franks"},
		]}],
	}
	ok = asyncio.run(civ_sync.record_lobby_match(
		9, 77,
		[(1, "A", 0), (2, "B", 1), (3, "C", 1), (4, "D", 0)],
		winner=0,
		match_at=999,
		parsed=parsed,
		db_adapter=fake,
		uid_to_pids={1: [100], 2: [200], 3: [300], 4: [400]},
		nick_to_pids={},
	))

	assert ok is False
	assert fake.rows == []
