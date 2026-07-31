"""Stage 5d: the web reads the derived layer, and nothing it retired.

These drive the REAL handlers in bot/web.py against a fake adapter and assert on
the payload they return. That distinction is the point of the file: bot/web.py's
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
import types
from pathlib import Path

import pytest

import bot.web as web
from bot.derived import rollups
from bot.scouting_report import PENDING


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
	    `from core.database import db` at import and they all hold that one
	    object — so this reaches any module in the path that this list forgets.
	  * the module-level `db` attribute on each module the path actually uses.
	    tests/test_derived_refresh.py rebinds `rollups.db` to its own sqlite
	    double and never restores it, so by the time the full suite reaches this
	    file `rollups.db` is no longer the shared adapter and patching the
	    adapter alone would silently miss it. monkeypatch.setattr restores
	    whatever was there, leak included.
	"""
	from core.database import db as real_db
	for name in ("fetchall", "fetchone", "select", "select_one", "execute", "insert"):
		monkeypatch.setattr(real_db, name, getattr(fake, name), raising=False)
	for module in (web, rollups):
		monkeypatch.setattr(module, "db", fake)
	return fake


def request(**query):
	return types.SimpleNamespace(query=dict(query))


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
	""" Behavioural, not a grep: the handler is driven with an empty table while
	data/civ_elo_stats.csv is restored to disk. A surviving CSV fallback would
	serve April's frozen snapshot here. """
	csv_path = os.path.join(_REPO_ROOT, "data", "civ_elo_stats.csv")
	fake_csv = (
		"civ,games,winrate,games_player_elo_above_1000,winrate_player_elo_above_1000\n"
		"Franks,900,0.55,400,0.58\n")
	assert not os.path.exists(csv_path), "the stage-5c CSV is deleted; this test must not clobber a real one"
	Path(csv_path).write_text(fake_csv)
	try:
		install_db(monkeypatch, FakeDB(answers={"FROM civ_stats": []},
		                               rows={"communities": [COMMUNITY_ROW]}))
		with_community(monkeypatch)
		payload = asyncio.run(web.handle_civ_stats(request())).payload
	finally:
		os.remove(csv_path)

	assert payload["civs"] == []
	assert "Franks" not in json.dumps(payload)


def test_the_retired_civ_csv_is_gone_from_disk():
	assert not os.path.exists(os.path.join(_REPO_ROOT, "data", "civ_elo_stats.csv"))


def test_the_module_imports_no_csv_reader():
	""" `import csv` was there for one reason. Left behind, it is a loaded gun
	pointed at the next person who needs a quick data source. """
	src = Path(_REPO_ROOT, "bot", "web.py").read_text()
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
	assert report["medals"] == {"military_pct": 34, "villager_pct": 18, "games_ranked": 50}
	assert report["apm"]["median_avg"] == 62.5 and report["apm"]["games_avg"] == 38
	assert [s["key"] for s in report["strategies"]] == ["archer_rush", "knight_rush"]
	assert report["strategies"][0]["label"] == "Feudal archer poke"
	assert report["strategies"][0] == {"key": "archer_rush", "label": "Feudal archer poke",
	                                   "games": 12, "wins": 7, "losses": 5, "winrate": 58}
	assert report["spawns"][0]["label"] == "Near Enemy"
	assert report["units"][0]["label"] == "Crossbowman"


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
	""" Spelled once, in bot/scouting_report.py, so `/rank` and the web page
	cannot drift into two slightly different sentences. """
	src = Path(_REPO_ROOT, "bot", "web.py").read_text()
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


def test_the_peak_appears_on_its_own_once_buckets_arrive():
	rollup = dict(FULL_ROLLUP, apm={"median_avg": 62.5, "median_peak": 180,
	                                "games_avg": 38, "games_peak": 4})

	report = web._scouting_payload(rollup)

	assert report["apm"] == {"median_avg": 62.5, "games_avg": 38,
	                         "median_peak": 180, "games_peak": 4}


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
	src = Path(_REPO_ROOT, "bot", "web.py").read_text()
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
	src = Path(_REPO_ROOT, "bot", "web.py").read_text()
	assert "persona_store" not in src
	assert "import persona" not in src
	assert "rs_persona" not in src
	assert "derive_persona(" not in src


def test_the_spa_does_not_reference_a_removed_payload_field():
	""" The page is the other half of every repoint above: a field the API stopped
	sending, still read here, renders as undefined rather than as an error. """
	page = Path(_REPO_ROOT, "bot", "web_page.html").read_text()
	for gone in ("impact_profile.persona", "p.persona", "profile.scout_report",
	             "luckProfileForPlayer(", "luckBaseline(", "res.player_threshold",
	             "games_player_above", "winrate_team_below"):
		assert gone not in page, f"the SPA still reads {gone}"
	assert "scouting_report" in page, "the SPA must render the block that replaced them"
