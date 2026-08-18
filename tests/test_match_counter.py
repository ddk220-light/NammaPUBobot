import asyncio

import nammaoe2bot.pickup.stats as stats


class CounterTransaction:
	def __init__(self, counter=None, maximum=0):
		self.counter = counter
		self.maximum = maximum
		self.calls = []

	async def __aenter__(self):
		return self

	async def __aexit__(self, exc_type, exc, traceback):
		return False

	def transaction(self):
		return self

	async def fetchone(self, sql, args=None):
		self.calls.append(("fetchone", sql, list(args or [])))
		if "FROM match_counter" in sql:
			return None if self.counter is None else {"next_id": self.counter}
		return {"next_id": self.maximum}

	async def execute(self, sql, args=None):
		self.calls.append(("execute", sql, list(args or [])))
		return 1

	async def insert(self, table, row, on_duplicate=None):
		self.calls.append(("insert", table, dict(row)))
		return 1


def test_next_match_locks_and_advances_the_existing_counter(monkeypatch):
	db = CounterTransaction(counter=51)
	monkeypatch.setattr(stats, "db", db)

	assert asyncio.run(stats.next_match()) == 51
	assert db.calls == [
		("fetchone", "SELECT next_id FROM match_counter FOR UPDATE", []),
		("execute", "UPDATE match_counter SET next_id=%s WHERE next_id=%s", [52, 51]),
	]


def test_next_match_recovers_a_missing_counter_from_global_match_ids(monkeypatch):
	db = CounterTransaction(counter=None, maximum=800)
	monkeypatch.setattr(stats, "db", db)

	assert asyncio.run(stats.next_match()) == 800
	assert db.calls[-1] == ("insert", "match_counter", {"next_id": 801})
