"""Stage 5d: the web reads the derived layer, and nothing it retired.

These drive the REAL handlers in nammaoe2bot/web/server.py against a fake adapter and assert on
the payload they return. That distinction is the point of the file: nammaoe2bot/web/server.py's
existing tests (test_web_identity.py) parse the source with `ast`, which can tell
you a query mentions a table but never what an endpoint actually renders — and a
source-level check is exactly what let a frozen persona blurb and a CSV snapshot
ship as current data for two stages.

There is no pytest-asyncio in this repo. An `async def test_...` would be
SILENTLY SKIPPED and report as passing, so every coroutine below is driven from a
sync test with asyncio.run().
"""
import asyncio
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

import nammaoe2bot.web.server as web
from nammaoe2bot.derived import rollups
from nammaoe2bot.features.scouting.report import PENDING


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestHealth:
	""" /health is railway.toml's healthcheckPath. A 503 from it does not
	degrade anything gracefully — Railway kills the container and redeploys,
	over and over.

	It read `getattr(bot, 'bot_ready', False)` for its Discord gate. Phase 1
	moved that global onto Application and the getattr DEFAULT swallowed the
	change: `discord_ok` became permanently False, so the endpoint would have
	answered 503 on every probe of every deploy. Nothing raised, no test
	failed, and the payload it returned was well-formed and wrong.

	These drive the real handler. `db_ok` is left failing in both cases — the
	fake adapter raises on fetchone — so the healthy case pins the DB half too
	rather than passing for the wrong reason. """

	def _request(self, monkeypatch, ready, matches, db_answers):
		async def _fetchone(*_a, **_k):
			if not db_answers:
				raise RuntimeError("db down")
			return {"ok": 1}

		# handle_health reaches two modules by function-local import, purely to
		# read one timestamp off each. Importing them for real pulls in the
		# whole Discord layer and the config factory, so they are pre-seeded in
		# sys.modules instead — `from pkg import name` binds whatever is
		# already there. The alternative is a nextcord fake big enough to load
		# the bot, which is a larger lie than these two floats.
		monkeypatch.setitem(sys.modules, "nammaoe2bot.discord.events",
							types.SimpleNamespace(last_tick_at=time.time()))
		monkeypatch.setitem(sys.modules, "nammaoe2bot.features.elo_sync",
							types.SimpleNamespace(last_elo_sync_at=0.0))
		monkeypatch.setattr(web.db, "fetchone", _fetchone, raising=False)
		monkeypatch.setattr(web.dc, "is_ready", lambda: ready, raising=False)
		web.dc.app.ready = ready
		web.dc.app.active_matches = list(matches)
		return asyncio.run(web.handle_health(types.SimpleNamespace()))

	def test_a_connected_bot_with_a_live_db_is_healthy(self, monkeypatch):
		resp = self._request(monkeypatch, ready=True, matches=[1, 2], db_answers=True)
		assert resp.status == 200
		assert resp.payload["status"] == "ok"
		assert resp.payload["bot_ready"] is True
		assert resp.payload["active_matches"] == 2

	def test_a_disconnected_bot_is_unhealthy(self, monkeypatch):
		resp = self._request(monkeypatch, ready=False, matches=[], db_answers=True)
		assert resp.status == 503
		assert resp.payload["discord_connected"] is False

	def test_a_dead_database_is_unhealthy_even_with_discord_up(self, monkeypatch):
		resp = self._request(monkeypatch, ready=True, matches=[], db_answers=False)
		assert resp.status == 503
		assert resp.payload["db_connected"] is False


def test_the_server_can_actually_find_its_page():
	""" HTML_PATH is built from __file__ and the dashboard has a FALLBACK for a
	missing file — `_html_cache = "<h1>page.html not found</h1>"` — so a wrong
	path serves that string with a 200 instead of raising. Moving web_page.html
	to web/page.html left the filename in HTML_PATH untouched and every test in
	this suite still passed, because they all read the page by their own
	literal path rather than by the server's.

	This asserts the server's own answer, which is the only one that matters at
	runtime. """
	assert os.path.isfile(web.HTML_PATH), (
		f"nammaoe2bot/web/server.py points HTML_PATH at {web.HTML_PATH}, which does not "
		f"exist — the dashboard would serve its not-found placeholder")



class FakeDB:
	"""Records every statement issued and answers from a marker -> rows map.

	A marker is a distinctive substring of the SQL ("FROM civ_stats"). An
	unmatched query returns nothing and is still recorded, so a test can assert
	on what was ASKED as well as on what came back — which is how the "no
	endpoint touches a retired table any more" tests below work.
	"""

	def __init__(self, answers=None, rows=None):
		self.answers = answers or {}
		self.rows = rows or {}
		self.sql = []
		self.selects = []
		self.inserted = []
		self.deleted = []

	# --- raw SQL ---
	def _answer(self, sql, args):
		for marker, rows in self.answers.items():
			if marker in sql:
				return list(rows(args) if callable(rows) else rows)
		return []

	async def fetchall(self, sql, args=None):
		self.sql.append(sql)
		return self._answer(sql, args or [])

	async def fetchone(self, sql, args=None):
		self.sql.append(sql)
		found = self._answer(sql, args or [])
		return found[0] if found else None

	# --- adapter helpers ---
	def _matching(self, table, where):
		out = []
		for row in self.rows.get(table) or []:
			if all(row.get(k) == v for k, v in (where or {}).items()):
				out.append(row)
		return out

	async def select(self, columns, table, where=None):
		self.selects.append((table, dict(where or {})))
		return [{c: row.get(c) for c in columns} for row in self._matching(table, where)]

	async def select_one(self, columns, table, where=None):
		found = await self.select(columns, table, where)
		return found[0] if found else None

	async def execute(self, sql, args=None):
		self.sql.append(sql)
		return None

	async def insert(self, table, row, on_duplicate=None):
		self.selects.append((table, dict(row)))
		self.inserted.append((table, dict(row)))
		return None

	async def delete(self, table, where=None):
		self.deleted.append((table, dict(where or {})))
		return None

	@property
	def statements(self):
		""" Everything issued, raw SQL and adapter-helper calls alike, as text —
		so a "never touches table X" assertion cannot be fooled by a read that
		happened to go through select_one() instead of fetchall(). """
		return self.sql + [f"{table} {sorted(where)}" for table, where in self.selects]


def install_db(monkeypatch, fake):
	""" Point the whole web read path at `fake`, two ways, because one is not
	enough:

	  * the METHODS on the shared adapter instance, since every module did
	    `from nammaoe2bot.runtime.database import db` at import and they all hold that one
	    object — so this reaches any module in the path that this list forgets.
	  * the module-level `db` attribute on each module the path actually uses,
	    since a module that rebound its own `db` no longer resolves through the
	    shared instance at all. tests/test_derived_refresh.py used to rebind
	    `rollups.db` to its own sqlite double and never restore it, which made
	    this second pass load-bearing rather than belt-and-braces; that file now
	    restores what it patched, and this stays because the general point
	    (module-level references outlive an instance patch) is unchanged.
	"""
	from nammaoe2bot.runtime.database import db as real_db
	for name in ("fetchall", "fetchone", "select", "select_one", "execute", "insert", "delete"):
		monkeypatch.setattr(real_db, name, getattr(fake, name), raising=False)
	for module in (web, rollups):
		monkeypatch.setattr(module, "db", fake)
	return fake


def request(cookies=None, match_info=None, headers=None, **query):
	""" The slice of aiohttp's Request the handlers below actually read. """
	return types.SimpleNamespace(
		query=dict(query),
		cookies=dict(cookies or {}),
		match_info=dict(match_info or {}),
		headers=dict(headers or {}),
		scheme="https",
		host="example.test",
	)


def with_community(monkeypatch, guild_id=777):
	""" Configure a flagship guild and give it a `communities` row, so
	_public_community_id resolves. Returns the community_id. """
	monkeypatch.setattr(web.cfg, "FLAGSHIP_GUILD_IDS", [guild_id], raising=False)
	return 9


COMMUNITY_ROW = {"guild_id": 777, "community_id": 9}


# A rollup shaped exactly like production's: peak_eapm is NULL on every row, so
# median_peak is null and games_peak is 0.
FULL_ROLLUP = {
	"medal_rates": {"military": 0.34, "villager": 0.18, "games_ranked": 50},
	"apm": {"median_avg": 62.5, "median_peak": None, "games_avg": 38, "games_peak": 0},
	"strategies": [{"key": "archer_rush", "games": 12, "wins": 7},
	               {"key": "knight_rush", "games": 6, "wins": 2}],
	"spawns": [{"key": "spawn_near_enemy", "games": 9, "wins": 4}],
	"units": [{"unit": "Crossbowman", "games": 11, "wins": 6}],
	"window_days": 60,
	"baseline": {"games": 93, "wins": 44},
}


# ─── /api/civ-stats reads the table, and no CSV is left anywhere ───

def test_civ_stats_renders_the_communitys_stored_tallies(monkeypatch):
	fake = install_db(monkeypatch, FakeDB(
		answers={"FROM civ_stats": [
			{"civ": "Franks", "games": 120, "wins": 66, "losses": 54},
			{"civ": "Mayans", "games": 80, "wins": 32, "losses": 48},
		]},
		rows={"communities": [COMMUNITY_ROW]}))
	with_community(monkeypatch)

	payload = asyncio.run(web.handle_civ_stats(request())).payload

	assert [c["civ"] for c in payload["civs"]] == ["Franks", "Mayans"]
	assert payload["civs"][0] == {"civ": "Franks", "games": 120, "wins": 66, "losses": 54,
	                              "winrate": 66 / 120}
	assert payload["min_games"] == 50
	assert any("FROM civ_stats" in s for s in fake.sql)


def test_civ_stats_scopes_the_read_to_one_community(monkeypatch):
	""" The community_id has to reach the WHERE clause. Summing every
	community's rows is the one number the derived-community layer exists to
	stop us printing. """
	seen = {}

	def capture(args):
		seen["args"] = list(args)
		return []

	install_db(monkeypatch, FakeDB(answers={"FROM civ_stats": capture},
	                               rows={"communities": [COMMUNITY_ROW]}))
	with_community(monkeypatch)

	asyncio.run(web.handle_civ_stats(request()))

	assert seen["args"][0] == 9
	assert seen["args"][1] == web.MIN_GAMES


def test_civ_stats_with_no_community_returns_nothing_rather_than_every_communitys_rows(monkeypatch):
	fake = install_db(monkeypatch, FakeDB(
		answers={"FROM civ_stats": [{"civ": "Franks", "games": 120, "wins": 66, "losses": 54}]},
		rows={"communities": []}))
	monkeypatch.setattr(web.cfg, "FLAGSHIP_GUILD_IDS", [], raising=False)

	payload = asyncio.run(web.handle_civ_stats(request())).payload

	assert payload["civs"] == []
	assert not any("FROM civ_stats" in s for s in fake.sql)


def test_civ_stats_reads_no_file_at_all(monkeypatch):
	""" Behavioural, not a grep: the handler is driven with an empty table and
	every file it opens is recorded. Nothing under data/ may be among them.

	This used to plant a fake data/civ_elo_stats.csv in the TRACKED data
	directory and delete it in a finally. An interrupted run left the file
	behind, which then failed the "the CSV is gone from disk" test on every
	later run, and two workers under pytest-xdist raced each other for it.
	Watching the opens is hermetic, leaves nothing on disk, and is strictly
	stronger: it catches a JSON or pickle fallback too, not just the one
	filename the planted CSV would have answered. """
	import builtins

	opened = []
	real_open = builtins.open

	def _recording_open(file, *a, **kw):
		opened.append(str(file))
		return real_open(file, *a, **kw)

	monkeypatch.setattr(builtins, "open", _recording_open)
	install_db(monkeypatch, FakeDB(answers={"FROM civ_stats": []},
	                               rows={"communities": [COMMUNITY_ROW]}))
	with_community(monkeypatch)
	payload = asyncio.run(web.handle_civ_stats(request())).payload

	data_dir = os.path.join(_REPO_ROOT, "data")
	from_disk = [p for p in opened if os.path.abspath(p).startswith(data_dir)]
	assert from_disk == [], f"/api/civ-stats read {from_disk} — a frozen snapshot is not this community's data"
	assert payload["civs"] == []


def test_the_retired_civ_csv_is_gone_from_disk():
	assert not os.path.exists(os.path.join(_REPO_ROOT, "data", "civ_elo_stats.csv"))


def test_the_module_imports_no_csv_reader():
	""" `import csv` was there for one reason. Left behind, it is a loaded gun
	pointed at the next person who needs a quick data source. """
	src = Path(_REPO_ROOT, "nammaoe2bot", "web", "server.py").read_text()
	assert "\nimport csv" not in src
	assert "csv.DictReader" not in src
	assert "civ_elo_stats.csv'" not in src and 'civ_elo_stats.csv"' not in src


# ─── the strategies page reads game_labels ───

_STRATEGY_ROWS = [
	{"k": "archer_rush", "player": "Alice", "games": 10, "wins": 6, "losses": 4},
	{"k": "archer_rush", "player": "Bob", "games": 4, "wins": 1, "losses": 3},
	{"k": "knight_rush", "player": "Alice", "games": 3, "wins": 3, "losses": 0},
]


def _strategy_db():
	return FakeDB(answers={
		"rp.identity AS player": _STRATEGY_ROWS,
		"gs.civ AS civ": [{"k": "archer_rush", "civ": "Britons", "n": 8},
		                  {"k": "archer_rush", "civ": "Mayans", "n": 2}],
		"FROM replay_players WHERE identity": [
			{"identity": "Alice", "games": 40, "wins": 22, "losses": 18}],
		"COUNT(DISTINCT gl.replay_match_id) AS g": [
			{"identity": "Alice", "g": 13, "w": 9, "l": 4}],
	})


def test_strategies_page_renders_rosters_from_game_labels(monkeypatch):
	fake = install_db(monkeypatch, _strategy_db())

	payload = asyncio.run(web.handle_strategies(request())).payload

	archer = next(s for s in payload["strategies"] if s["key"] == "archer_rush")
	assert archer["games"] == 14
	assert archer["wins"] == 7 and archer["losses"] == 7
	assert [p["player"] for p in archer["roster"]] == ["Alice", "Bob"]
	assert archer["top_civs"] == ["Britons", "Mayans"]
	assert payload["player_totals"]["Alice"] == {"games": 40, "wins": 22, "losses": 18}
	assert payload["player_categorized"]["Alice"] == {"games": 13, "wins": 9, "losses": 4}
	assert any("FROM game_labels" in s for s in fake.sql)


def test_every_strategies_query_constrains_the_stored_kind(monkeypatch):
	""" Without the kind in the WHERE, the spawn rows sharing the table would be
	counted as strategies. """
	fake = install_db(monkeypatch, _strategy_db())

	asyncio.run(web.handle_strategies(request()))

	label_reads = [s for s in fake.sql if "FROM game_labels" in s]
	assert label_reads, "the strategies page issued no game_labels query at all"
	for sql in label_reads:
		assert "gl.kind=%s" in sql


def test_strategies_page_emits_no_luck_row(monkeypatch):
	""" The luck keys are registered upstream but stored under no kind, so a row
	for one could only ever be a row of zeros. """
	install_db(monkeypatch, _strategy_db())

	payload = asyncio.run(web.handle_strategies(request())).payload

	assert {s["category"] for s in payload["strategies"]} == {"strategy"}
	keys = {s["key"] for s in payload["strategies"]}
	assert "luck_baseline" not in keys
	assert not any(k.startswith("spawn_") for k in keys)
	assert "archer_rush" in keys


def test_strategy_roster_join_reads_the_three_tables_by_their_own_keys(monkeypatch):
	""" game_stats on (match, player_number) — game_labels' grain minus the
	label — and replay_players on (match, profile_id), its own PK. Joining
	replay_players on player_number instead would duplicate rows. """
	fake = install_db(monkeypatch, _strategy_db())

	asyncio.run(web.handle_strategies(request()))

	sql = next(s for s in fake.sql if "rp.identity AS player" in s)
	assert "gs.player_number=gl.player_number" in sql
	assert "rp.profile_id=gs.profile_id" in sql
	assert "rp.player_number" not in sql


# ─── the player API renders from player_rollups ───

def _player_db(rollup_rows):
	return FakeDB(
		answers={
			"FROM player_ratings WHERE user_id": [],
			"FROM match_players pm WHERE pm.user_id": [{"x": 1}],
		},
		rows={"communities": [COMMUNITY_ROW], "player_rollups": rollup_rows})


def test_player_scouting_report_is_rendered_from_the_rollup(monkeypatch):
	install_db(monkeypatch, _player_db([
		{"community_id": 9, "user_id": 42, "rollup": json.dumps(FULL_ROLLUP)}]))
	with_community(monkeypatch)

	report = asyncio.run(web._player_scouting_report(42))

	assert report["pending"] is None
	assert report["medals"] == {"military_per_game": 0.34, "villager_per_game": 0.18,
	                            "games_ranked": 50}
	assert report["apm"]["median_avg"] == 62.5 and report["apm"]["games_avg"] == 38
	assert report["window_days"] == 60 and report["window_games"] == 93
	assert [s["key"] for s in report["strategies"]] == ["archer_rush", "knight_rush"]
	assert report["strategies"][0]["label"] == "Feudal archer poke"
	assert report["strategies"][0] == {"key": "archer_rush", "label": "Feudal archer poke",
	                                   "games": 12, "wins": 7, "losses": 5, "winrate": 58}
	assert report["spawns"][0]["label"] == "Near Enemy"
	assert report["units"][0]["label"] == "Crossbowman"
	# The clauses are CHOSEN server-side, so the page never re-ranks. With a
	# 47% baseline, archer_rush (58%) is the strength and knight_rush (33%) the
	# weakness -- the shrunk rate, not the row order.
	assert [h["key"] for h in report["highlights"]["wins_most"]] == [
		"archer_rush", "spawn_near_enemy", "Crossbowman"]
	assert [h["key"] for h in report["highlights"]["loses_most"]] == ["knight_rush"]


def test_the_scouting_report_reads_the_rollup_for_the_resolved_community(monkeypatch):
	fake = install_db(monkeypatch, _player_db([
		{"community_id": 9, "user_id": 42, "rollup": json.dumps(FULL_ROLLUP)}]))
	with_community(monkeypatch)

	asyncio.run(web._player_scouting_report(42))

	assert ("player_rollups", {"community_id": 9, "user_id": 42}) in fake.selects


def test_a_player_with_no_rollup_row_yields_exactly_the_pending_string(monkeypatch):
	""" The design's stage-5 acceptance criterion. An unlinked player has NO row
	rather than a row of zeros, so the absence is the signal. """
	install_db(monkeypatch, _player_db([]))
	with_community(monkeypatch)

	report = asyncio.run(web._player_scouting_report(42))

	assert report == {"pending": PENDING}
	assert report["pending"] == "Statistics pending linking"
	# Nothing that could render as a measurement of zero.
	assert "0" not in json.dumps(report).replace("Statistics pending linking", "")


def test_the_pending_string_is_the_one_discord_prints():
	""" Spelled once, in nammaoe2bot/features/scouting/report.py, so `/rank` and the web page
	cannot drift into two slightly different sentences. """
	src = Path(_REPO_ROOT, "nammaoe2bot", "web", "server.py").read_text()
	assert "scouting_report.PENDING" in src
	assert '"Statistics pending linking"' not in src


def test_no_community_omits_the_block_rather_than_claiming_a_linking_gap(monkeypatch):
	""" "Nothing was ever measured here" and "this player is not linked" are
	different statements and must not collapse into one. """
	install_db(monkeypatch, _player_db([]))
	monkeypatch.setattr(web.cfg, "FLAGSHIP_GUILD_IDS", [], raising=False)

	assert asyncio.run(web._player_scouting_report(42)) is None


def test_the_peak_eapm_is_omitted_while_no_peak_was_ever_captured():
	""" Production has peak_eapm NULL on every row: games_peak is 0. The peak
	keys must be ABSENT, not null and not zero — a rendered blank or a 0 reads
	as a measured peak of nothing. """
	report = web._scouting_payload(FULL_ROLLUP)

	assert report["apm"] == {"median_avg": 62.5, "games_avg": 38}
	assert "median_peak" not in report["apm"]
	assert "games_peak" not in report["apm"]


def test_the_peak_is_held_back_until_its_own_sample_clears_the_floor():
	""" The peak carries its own count, so it gets the floor separately: a
	median over 38 games beside a peak over 2 quotes two very different
	confidences in one line without saying so. """
	rollup = dict(FULL_ROLLUP, apm={"median_avg": 62.5, "median_peak": 180,
	                                "games_avg": 38, "games_peak": 2})

	assert web._scouting_payload(rollup)["apm"] == {"median_avg": 62.5, "games_avg": 38}


def test_the_peak_appears_on_its_own_once_buckets_arrive():
	rollup = dict(FULL_ROLLUP, apm={"median_avg": 62.5, "median_peak": 180,
	                                "games_avg": 38, "games_peak": 9})

	report = web._scouting_payload(rollup)

	assert report["apm"] == {"median_avg": 62.5, "games_avg": 38,
	                         "median_peak": 180, "games_peak": 9}


def test_a_split_smaller_than_the_players_game_count_is_not_reconciled():
	""" Unresolved outcomes count as games played but are excluded from splits,
	so a split's games is legitimately smaller. Nothing may 'fix' that. """
	report = web._scouting_payload(FULL_ROLLUP)

	assert sum(s["games"] for s in report["strategies"]) == 18
	assert report["strategies"][0]["games"] + report["strategies"][0]["losses"] > 0
	for row in report["strategies"] + report["spawns"] + report["units"]:
		assert row["losses"] == row["games"] - row["wins"]


def test_a_rollup_with_no_measurable_block_reports_nothing_rather_than_zeros():
	""" A linked player whose every line is below its floor. Saying PENDING here
	would be a lie about a linked player; saying 0% would be a lie about an
	unmeasured one. """
	report = web._scouting_payload({
		"medal_rates": {"military": None, "villager": None, "games_ranked": 0},
		"apm": {"median_avg": None, "median_peak": None, "games_avg": 0, "games_peak": 0},
		"strategies": [], "spawns": [], "units": [],
	})

	assert report["pending"] is None
	assert report["medals"] is None
	assert report["apm"] is None
	assert report["strategies"] == [] and report["spawns"] == [] and report["units"] == []


def test_a_malformed_rollup_blob_raises_rather_than_rendering_an_empty_report(monkeypatch):
	install_db(monkeypatch, _player_db([
		{"community_id": 9, "user_id": 42, "rollup": "{not json"}]))
	with_community(monkeypatch)

	with pytest.raises(json.JSONDecodeError):
		asyncio.run(web._player_scouting_report(42))


def test_the_scouting_block_reaches_the_player_payload(monkeypatch):
	""" _scouting_payload being right is worth nothing if the handler drops it. """
	src = Path(_REPO_ROOT, "nammaoe2bot", "web", "server.py").read_text()
	assert src.count('"scouting_report": scouting') == 2, \
		"both /api/player-stats and /api/match-stats?player_id= must carry the block"


# ─── the player API's strategy tags read game_labels, windowed ───

def test_player_strategy_tags_read_game_labels_within_the_selected_window(monkeypatch):
	captured = {}

	def capture(args):
		captured["args"] = list(args)
		return [{"key": "archer_rush", "games": 9, "wins": 5, "losses": 4}]

	fake = install_db(monkeypatch, FakeDB(answers={"FROM game_labels": capture}))

	tags = asyncio.run(web._player_strategy_tags([123, 456], "month"))

	assert tags[0]["key"] == "archer_rush"
	assert tags[0]["label"] == "Feudal archer poke"
	assert tags[0]["winrate"] == 56
	sql = fake.sql[0]
	assert "gl.kind=%s" in sql and "gs.profile_id IN" in sql and "gl.played_at >= %s" in sql
	assert captured["args"][:3] == ["strategy", "123", "456"]


def test_player_strategy_tags_skip_the_window_clause_for_all_time(monkeypatch):
	fake = install_db(monkeypatch, FakeDB(answers={"FROM game_labels": []}))

	asyncio.run(web._player_strategy_tags([123], "all"))

	assert "gl.played_at" not in fake.sql[0]


def test_player_strategy_tags_are_not_served_from_the_lifetime_rollup(monkeypatch):
	""" The page has a period selector; the rollup has no time dimension.
	Serving lifetime numbers under a 3-month filter is the quiet lie this
	migration exists to remove. """
	fake = install_db(monkeypatch, FakeDB(answers={"FROM game_labels": []}))

	asyncio.run(web._player_strategy_tags([123], "month3"))

	assert not any("player_rollups" in s for s in fake.statements)


# ─── per-match chips ───

def test_match_strategy_chips_come_from_game_labels(monkeypatch):
	fake = install_db(monkeypatch, FakeDB(answers={
		"JOIN game_labels gl": [
			{"bot_match_id": 7, "profile_id": 111, "identity": "Alice", "key": "scout_rush"}],
		"JOIN rs_player_game_tags": [],
	}))

	by_profile, by_name = asyncio.run(web._classification_tags_for_bot_matches([7]))

	assert by_profile[(7, "111")] == [{"key": "scout_rush", "label": "Scout-map opener"}]
	assert by_name[(7, "alice")] == [{"key": "scout_rush", "label": "Scout-map opener"}]
	chip_sql = fake.sql[0]
	assert "JOIN game_labels gl" in chip_sql and "gl.kind=%s" in chip_sql


# ─── the removals ───

def test_the_luck_route_is_not_registered():
	""" Unregistered means aiohttp answers 404, which is the honest reply to a
	bookmarked link — registering it would serve the SPA shell for a page whose
	data source is gone. """
	paths = {path for _method, path, _handler in web.create_app().router.routes}

	assert "/luck" not in paths
	assert "/strategies" in paths, "the surviving SPA routes must still be registered"
	assert "/api/civ-stats" in paths


def test_no_endpoint_reads_a_table_stage_six_drops(monkeypatch):
	""" Drives every public endpoint and inspects what was actually asked of the
	database, so a read cannot hide behind a helper.

	The match-list answers below are load-bearing, not scenery. Every
	per-match helper (impacts, rosters, strategy chips) returns early on an
	empty id list, so an endpoint driven against a wholly empty database never
	reaches the queries this test exists to inspect — it would pass against a
	chip query still pointed at a retired table. """
	match_row = {"match_id": 7, "queue_name": "pickup", "at": 0, "ranked": 1,
	             "winner": None, "maps": "Arabia", "team": 0, "duration_s": 1800}
	fake = install_db(monkeypatch, FakeDB(
		answers={
			"FROM match_players pm WHERE pm.user_id": [{"x": 1}],
			"ORDER BY m.reported_at DESC": [match_row],
			"SELECT DISTINCT m.match_id": [{"match_id": 7}],
		},
		rows={"communities": [COMMUNITY_ROW]}))
	with_community(monkeypatch)

	asyncio.run(web.handle_civ_stats(request()))
	asyncio.run(web.handle_strategies(request()))
	asyncio.run(web.handle_leaderboard(request(mode="tags")))
	asyncio.run(web.handle_leaderboard(request()))
	asyncio.run(web.handle_match_stats(request()))
	asyncio.run(web.handle_match_stats(request(player_id="42")))
	asyncio.run(web.handle_player_stats(request(player_id="42")))

	assert fake.statements, "no endpoint issued a single query — the drive-through is broken"
	retired = ("cls_results", "cls_classifications", "cls_result_metrics",
	           "cls_player_totals", "cls_match_ingest", "rs_player_personas")
	for statement in fake.statements:
		for name in retired:
			assert name not in statement, f"{name} is still read: {statement}"
	assert any("JOIN game_labels gl" in s for s in fake.sql), \
		"the per-match chip query never ran, so this sweep proves nothing about it"


def test_the_player_page_carries_no_generated_persona(monkeypatch):
	""" Both ends of it: the frozen stored overlay and the live derivation. """
	install_db(monkeypatch, _player_db([]))
	with_community(monkeypatch)

	payload = asyncio.run(web.handle_player_stats(request(player_id="42"))).payload
	profile = payload["summary"]["impact_profile"]

	assert "persona" not in profile
	assert "scout_report" not in profile
	assert "persona" not in json.dumps(payload)


def test_the_persona_modules_are_no_longer_imported():
	src = Path(_REPO_ROOT, "nammaoe2bot", "web", "server.py").read_text()
	assert "persona_store" not in src
	assert "import persona" not in src
	assert "rs_persona" not in src
	assert "derive_persona(" not in src


def test_the_spa_does_not_reference_a_removed_payload_field():
	""" The page is the other half of every repoint above: a field the API stopped
	sending, still read here, renders as undefined rather than as an error. """
	page = Path(_REPO_ROOT, "nammaoe2bot", "web", "page.html").read_text()
	for gone in ("impact_profile.persona", "p.persona", "profile.scout_report",
	             "luckProfileForPlayer(", "luckBaseline(", "res.player_threshold",
	             "games_player_above", "winrate_team_below"):
		assert gone not in page, f"the SPA still reads {gone}"
	assert "scouting_report" in page, "the SPA must render the block that replaced them"


# ─── HTTP status codes are part of the contract, not decoration ───
# ~30 responses in nammaoe2bot/web/server.py carry an explicit status (400/401/403/404/503) and
# not one was asserted, so rewriting the fake's json_response to DISCARD the
# caller's status passed the whole suite. The SPA branches on status — a 401
# sends it to the login screen — so an auth regression returning
# {"error": "Not logged in"} with status 200 would have shipped silently.

def test_the_response_fake_cannot_quietly_lose_a_status(monkeypatch):
	""" Guarding the guard. Every assertion below is worthless if the fake
	drops what it is handed, and that is exactly the mutation that shipped
	green. """
	assert web.web.json_response({"error": "x"}, status=403).status == 403
	assert web.web.json_response({"ok": True}).status == 200
	with pytest.raises(TypeError):
		web.web.json_response({}, status="403")


def test_a_malformed_player_id_is_a_400_not_an_empty_200(monkeypatch):
	install_db(monkeypatch, FakeDB())

	response = asyncio.run(web.handle_player_stats(request(player_id="not-a-number")))

	assert response.status == 400
	assert response.payload == {"error": "Invalid player_id"}


def test_a_missing_player_id_is_a_400(monkeypatch):
	install_db(monkeypatch, FakeDB())

	response = asyncio.run(web.handle_player_stats(request()))

	assert response.status == 400
	assert response.payload == {"error": "Missing player_id"}


def test_an_unknown_player_is_a_404(monkeypatch):
	install_db(monkeypatch, FakeDB())

	async def _no_such_player(_user_id):
		return False

	monkeypatch.setattr(web, "_player_has_public_stats", _no_such_player)
	response = asyncio.run(web.handle_player_stats(request(player_id="42")))

	assert response.status == 404
	assert response.payload == {"error": "Player not found"}


def _session_row(session_id="sess", user_id=42, expires_in=3600):
	return {"session_id": session_id, "user_id": user_id, "username": "someone",
	        "avatar": None, "csrf": "tok", "expires_at": int(time.time()) + expires_in}


def test_an_anonymous_dashboard_read_is_a_401_not_an_empty_list(monkeypatch):
	""" The distinction the SPA acts on: 401 means "log in", an empty 200 means
	"you are logged in and own nothing". """
	install_db(monkeypatch, FakeDB())

	response = asyncio.run(web.handle_api_guilds(request()))

	assert response.status == 401
	assert response.payload == {"error": "Not logged in"}


def test_an_expired_session_is_also_a_401(monkeypatch):
	install_db(monkeypatch, FakeDB(rows={"web_sessions": [_session_row(expires_in=-1)]}))

	response = asyncio.run(web.handle_api_guilds(request(cookies={web.COOKIE_NAME: "sess"})))

	assert response.status == 401


def test_a_logged_in_user_asking_about_an_unknown_guild_is_a_404(monkeypatch):
	""" Drives past the auth gate on a real session row, so the 404 is the
	guild branch rather than the login one. dc.get_guild answers None here,
	which is the state a process with no Discord connection is actually in. """
	install_db(monkeypatch, FakeDB(rows={"web_sessions": [_session_row()]}))

	response = asyncio.run(web.handle_api_channels(
		request(cookies={web.COOKIE_NAME: "sess"}, match_info={"guild_id": "777"})))

	assert response.status == 404
	assert response.payload == {"error": "Guild not found"}


def test_every_error_branch_in_the_module_carries_a_status(monkeypatch):
	""" Source-level, and deliberately so: the four handlers above are the ones
	worth driving, but a NEW error branch added without a status would return
	200 with an error body and no test would notice. Any json_response whose
	payload is an {"error": ...} literal must name a status. """
	import ast

	tree = ast.parse(Path(_REPO_ROOT, "nammaoe2bot", "web", "server.py").read_text())
	naked = []
	for node in ast.walk(tree):
		if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
		        and node.func.attr == "json_response"):
			continue
		if not node.args or not isinstance(node.args[0], ast.Dict):
			continue
		keys = [k.value for k in node.args[0].keys if isinstance(k, ast.Constant)]
		if "error" not in keys:
			continue
		if not any(kw.arg == "status" for kw in node.keywords):
			naked.append(node.lineno)
	assert naked == [], f"error responses with no status (they return 200): lines {naked}"


# ─── the OAuth path, which the old fakes made untestable ───
# HTTPBadRequest/HTTPFound/HTTPForbidden/HTTPNotFound/HTTPUnauthorized used to be
# ONE class wearing five names, so isinstance(HTTPFound(...), HTTPNotFound) was
# True and pytest.raises could not fail; `location` was read from kwargs while
# every call site passes it positionally, so it was always None; and
# set_cookie/del_cookie did not exist, so login and logout AttributeError'd on
# contact. Nothing here could be written at all until conftest's fakes became
# distinct types with the real signatures.

def test_the_http_exception_fakes_are_distinct_types():
	""" The property every pytest.raises below rests on. """
	found = web.web.HTTPFound("/somewhere")
	assert not isinstance(found, web.web.HTTPNotFound)
	assert not isinstance(web.web.HTTPBadRequest(), web.web.HTTPForbidden)
	assert not isinstance(web.web.HTTPUnauthorized(), web.web.HTTPNotFound)
	assert (found.status, found.location) == (302, "/somewhere")
	assert (web.web.HTTPBadRequest().status, web.web.HTTPNotFound().status) == (400, 404)


def test_login_without_oauth_configured_is_a_400(monkeypatch):
	monkeypatch.setattr(web.cfg, "DC_CLIENT_SECRET", "", raising=False)

	with pytest.raises(web.web.HTTPBadRequest):
		asyncio.run(web.handle_auth_login(request()))


def test_login_redirects_to_discord_with_the_state_it_just_stored(monkeypatch):
	""" The redirect target is the whole point of the handler, and it was
	unreachable: `location` came from kwargs while nammaoe2bot/web/server.py passes it
	positionally, so the fake reported None no matter what was raised. """
	fake = install_db(monkeypatch, FakeDB())
	monkeypatch.setattr(web.cfg, "DC_CLIENT_SECRET", "shhh", raising=False)
	monkeypatch.setattr(web.cfg, "DC_CLIENT_ID", 1234, raising=False)
	monkeypatch.setattr(web.cfg, "WS_ROOT_URL", "https://pubobot.test/", raising=False)

	with pytest.raises(web.web.HTTPFound) as raised:
		asyncio.run(web.handle_auth_login(request()))

	location = raised.value.location
	assert location.startswith(web.DISCORD_OAUTH_AUTHORIZE + "?")
	stored = [row for table, row in fake.inserted if table == "web_oauth_states"]
	assert len(stored) == 1, "the state must be persisted before the user is sent to Discord"
	assert f"state={stored[0]['state']}" in location
	assert "redirect_uri=https%3A%2F%2Fpubobot.test%2Fauth%2Fcallback" in location


def test_the_callback_rejects_a_request_with_no_code(monkeypatch):
	install_db(monkeypatch, FakeDB())
	monkeypatch.setattr(web.cfg, "DC_CLIENT_SECRET", "shhh", raising=False)

	with pytest.raises(web.web.HTTPBadRequest):
		asyncio.run(web.handle_auth_callback(request()))


def test_the_callback_rejects_an_unknown_state(monkeypatch):
	""" Not merely "raises something": a state nobody issued must be a 400,
	which is only a meaningful assertion now that HTTPBadRequest is its own
	type. """
	install_db(monkeypatch, FakeDB(rows={"web_oauth_states": []}))
	monkeypatch.setattr(web.cfg, "DC_CLIENT_SECRET", "shhh", raising=False)

	with pytest.raises(web.web.HTTPBadRequest) as raised:
		asyncio.run(web.handle_auth_callback(request(code="abc", state="forged")))
	assert raised.value.status == 400


def test_the_callback_rejects_an_expired_state_and_drops_the_row(monkeypatch):
	expired = {"state": "old", "expires_at": int(time.time()) - 1}
	fake = install_db(monkeypatch, FakeDB(rows={"web_oauth_states": [expired]}))
	monkeypatch.setattr(web.cfg, "DC_CLIENT_SECRET", "shhh", raising=False)

	with pytest.raises(web.web.HTTPBadRequest):
		asyncio.run(web.handle_auth_callback(request(code="abc", state="old")))
	assert ("web_oauth_states", {"state": "old"}) in fake.deleted


def test_logout_clears_the_session_cookie_and_redirects_home(monkeypatch):
	""" del_cookie was absent from the fakes entirely, so this handler could not
	be reached without an AttributeError -- meaning "logout leaves the cookie
	in place" was unfalsifiable. """
	fake = install_db(monkeypatch, FakeDB(rows={"web_sessions": [_session_row()]}))

	with pytest.raises(web.web.HTTPFound) as raised:
		asyncio.run(web.handle_auth_logout(request(cookies={web.COOKIE_NAME: "sess"})))

	assert raised.value.location == "/"
	assert web.COOKIE_NAME in raised.value.deleted_cookies
	assert ("web_sessions", {"session_id": "sess"}) in fake.deleted


# ─── this file must not change what another file sees ───
# `import nammaoe2bot.web.server` at module scope runs during COLLECTION and pulls in
# nammaoe2bot.runtime.cfg_factory -> nammaoe2bot.runtime.utils, which builds Embeds at import time out of
# whatever sys.modules['nextcord'] holds. That cached nammaoe2bot.runtime.utils is then shared
# with every later file, so a fake defined here can decide what an unrelated
# file's assertions compare against. It stays correct only while there is
# exactly ONE definition of that fake.

def test_the_embed_fake_has_a_single_definition_in_the_suite():
	""" tests/test_identity.py and tests/test_scouting_report.py both used to
	carry their own copy, kept in step by a comment. Which one ended up behind
	nammaoe2bot.runtime.utils depended on collection order. """
	import nammaoe2bot.runtime.utils
	from tests import conftest

	assert nammaoe2bot.runtime.utils.Embed is conftest.FakeEmbed, (
		"nammaoe2bot.runtime.utils was imported against some other nextcord fake — whichever file "
		"collected first now decides what every Embed assertion in the suite compares "
		"against")

	# Built at runtime so this test's own source does not match itself.
	needles = ("class " + "_FakeEmbed", "class " + "FakeEmbed")
	defined_in = []
	for name in ("test_identity.py", "test_scouting_report.py", "test_web_repoint.py"):
		text = Path(_REPO_ROOT, "tests", name).read_text()
		if any(needle in text for needle in needles):
			defined_in.append(name)
	assert defined_in == [], (
		f"{defined_in} define their own Embed fake again; conftest.FakeEmbed is the one "
		f"definition, because nammaoe2bot.runtime.utils caches whichever it sees first")


def test_importing_this_file_leaves_the_shared_adapter_alone():
	""" Collection-time side effects are the other half of the same problem: a
	file that permanently rebinds a shipped module's `db` makes the suite pass
	in one order and fail in another. """
	from nammaoe2bot.runtime.database import db as real_db

	assert web.db is real_db, "nammaoe2bot.web.server.db was left pointing at a test double"
	assert rollups.db is real_db, "nammaoe2bot.derived.rollups.db was left pointing at a test double"
