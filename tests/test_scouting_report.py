"""The stage-5a scouting report: bot/scouting_report.py's renderer, the
player_rollups readers behind it, and the `/rank` wiring that chooses between
a report, the pending-linking notice and no field at all.

Most of the assertions below run the REAL aggregation (bot/derived/rollups.
compute_rollup) over game_stats/game_labels-shaped rows and render its output,
rather than hand-writing a tidy blob. Every copy rule this task exists to
enforce is a rule about the relationship between a number and the rows behind
it -- a rate and its denominator, a split and its floor, a median and its
sample -- and a hand-written blob asserts that relationship into existence
instead of testing it. Two of the four mutants these tests are written to kill
(medal rates over games played; the per-split floor removed) live in
compute_rollup, and only an end-to-end test reaches them.

No pytest-asyncio in this repo -- an `async def test_...` is collected and
SKIPPED, reporting green while asserting nothing -- so every coroutine here is
driven with asyncio.run().
"""
import ast
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import bot.community as community
import bot.derived.rollups as rollups
import bot.scouting_report as scouting_report
from bot.derived.rollups import SPLIT_MIN_GAMES, compute_rollup

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── fixtures ─────────────────────────────────────────────────────────────
# game_stats / game_labels row shapes, same as tests/test_rollups.py's.

def _stat(mid, winner=True, avg_eapm=50, peak_eapm=None, military_medal=None,
          villager_medal=None, units=("Knight",), has_production=True):
	return dict(
		replay_match_id=mid, player_number=1, profile_id=11,
		civ="Franks", team="1", winner=winner,
		avg_eapm=avg_eapm, peak_eapm=peak_eapm,
		military_medal=military_medal, villager_medal=villager_medal,
		has_production=has_production,
		top_units=[dict(unit=u, category="cavalry", total=10) for u in units],
		computed_at=1000,
	)


def _label(mid, key, kind):
	return dict(replay_match_id=mid, player_number=1, label=key, kind=kind,
	            evidence={}, played_at=500)


def _rich():
	"""A player with something to say on every line.

	12 games; 10 of them measured (`has_production`), of which 5 carry a
	military medal and 2 a villager medal -- so the medal denominator (10) is
	NOT the game count (12) and NOT the medal count. eAPM is present on 11 of
	the 12, peak on none of them (production's shape today). Two games have an
	unresolved outcome, so every split rests on 10 games while the player has
	played 12.

	Each block also carries a second, thinner key that must not reach the
	reader: the report prints the top one only."""
	stats, labels = [], []
	for i in range(1, 13):
		# Games 11 and 12 are unresolved; 9 and 10 were never measured.
		winner = True if i <= 5 else (False if i <= 10 else None)
		stats.append(_stat(
			i,
			winner=winner,
			avg_eapm=None if i == 12 else 40 + i,
			military_medal=1 if i <= 5 else None,
			villager_medal=2 if i <= 2 else None,
			has_production=i <= 10,
			units=("Knight",) if i <= 8 else ("Archer",),
		))
		# archer_rush on 6 resolved games (4 won), safe_castle on 3 -- below
		# the floor and therefore absent from the blob entirely.
		labels.append(_label(i, "archer_rush" if i <= 6 else "safe_castle", "strategy"))
		labels.append(_label(i, "spawn_near_enemy" if i <= 7 else "spawn_isolated", "spawn"))
	return compute_rollup(stats, labels)


def _blob(**overrides):
	"""A contract-shaped rollup for the renderer-only cases, where the point is
	a blob shape rather than the arithmetic that produced it."""
	blob = dict(medal_rates=dict(military=None, villager=None, games_ranked=0),
	            apm=dict(median_avg=None, median_peak=None, games_avg=0, games_peak=0),
	            strategies=[], spawns=[], units=[])
	blob.update(overrides)
	return blob


def _lines(text):
	return (text or "").split("\n")


def _line_with(text, needle):
	found = [ln for ln in _lines(text) if needle in ln]
	assert len(found) == 1, f"expected exactly one {needle!r} line in:\n{text}"
	return found[0]


# ── the pending-linking case ─────────────────────────────────────────────
# The design's own stage-5 acceptance criterion (identity v2 §5): an unlinked
# player has NO player_rollups row, and the absence is the signal.

def test_a_player_with_no_rollup_row_renders_exactly_the_pending_string():
	assert scouting_report.render(None) == "Statistics pending linking"


def test_the_pending_field_carries_nothing_else_at_all():
	""" Not a zeroed report beside it, not a hedge under it, not a digit
	anywhere: a row of zeros would be a measurement nobody took. """
	rendered = scouting_report.render(None)
	assert _lines(rendered) == ["Statistics pending linking"]
	assert not any(ch.isdigit() for ch in rendered)
	assert "%" not in rendered and "W-" not in rendered


def test_the_pending_string_goes_through_the_callers_translator():
	seen = []

	def gt(text):
		seen.append(text)
		return text.upper()

	assert scouting_report.render(None, gt) == "STATISTICS PENDING LINKING"
	assert seen == ["Statistics pending linking"]


def test_a_rollup_that_fills_no_line_renders_no_field_rather_than_pending():
	""" A linked player whose every line is below its floor is NOT the unlinked
	case. Saying "pending linking" at them would be false, and saying "0%"
	would report a measurement nobody took -- so the field is omitted. """
	assert scouting_report.render(_blob()) is None


# ── medal rates ──────────────────────────────────────────────────────────

def test_medal_rates_render_over_the_ranked_denominator_not_games_played():
	""" games_ranked (10) is neither the game count (12) nor the medal count.
	Dividing by games played would print 42%/17% here. """
	rendered = scouting_report.render(_rich())
	medals = _line_with(rendered, "military")

	assert "**50%** military" in medals
	assert "**20%** villager" in medals
	assert "over 10 ranked games" in medals
	assert "42%" not in medals and "17%" not in medals


def test_the_medal_denominator_is_always_printed_beside_the_rates():
	""" The binding copy rule. Two rates that sum to 70% are only legible
	against the sample they were taken over. """
	rendered = scouting_report.render(_rich())
	medals = _line_with(rendered, "military")

	assert "ranked games" in medals
	assert str(_rich()["medal_rates"]["games_ranked"]) in medals


def test_the_medal_line_is_omitted_when_no_game_was_ever_ranked():
	""" compute_rollup returns None rather than 0.0 for a player nothing was
	scored for: "never medals" and "never measured" are different claims. """
	rollup = compute_rollup([_stat(1, has_production=False)], [])
	assert rollup["medal_rates"] == dict(military=None, villager=None, games_ranked=0)
	assert "military" not in (scouting_report.render(rollup) or "")


# ── eAPM ─────────────────────────────────────────────────────────────────

def test_the_apm_line_omits_the_peak_entirely_while_none_was_captured():
	""" peak_eapm is NULL on all 8885 production rows, so median_peak is null
	and games_peak is 0. Nothing may stand in for it -- not a blank, not an
	em-dash, not a zero. """
	rollup = _rich()
	assert (rollup["apm"]["median_peak"], rollup["apm"]["games_peak"]) == (None, 0)

	apm = _line_with(scouting_report.render(rollup), "eAPM")
	assert "peak" not in apm.lower()
	assert "—" not in apm and "--" not in apm
	assert "0" not in apm.replace("**", "")


def test_the_apm_line_carries_its_own_sample_count_not_the_game_count():
	""" 11 games have an eAPM, 12 were played. The line rests on 11. """
	apm = _line_with(scouting_report.render(_rich()), "eAPM")
	assert "over 11 games" in apm


def test_the_peak_appears_with_its_own_count_once_buckets_arrive():
	""" Two independent samples, never one shared figure: the day peaks start
	being captured they will be captured for fewer games than the averages. """
	stats = [_stat(i, peak_eapm=100 + i if i <= 3 else None, avg_eapm=50 + i) for i in range(1, 6)]
	rollup = compute_rollup(stats, [])
	assert (rollup["apm"]["median_peak"], rollup["apm"]["games_peak"]) == (102, 3)

	apm = _line_with(scouting_report.render(rollup), "eAPM")
	assert "peak **102** over 3" in apm
	assert "over 5 games" in apm


def test_a_half_median_is_not_rounded_away():
	""" compute_rollup deliberately does not round an even-length sample to an
	integer: 62 and 62.5 are different samples. """
	rollup = compute_rollup([_stat(1, avg_eapm=60), _stat(2, avg_eapm=65)], [])
	assert "**62.5** median" in _line_with(scouting_report.render(rollup), "eAPM")


def test_a_whole_median_renders_without_a_pointless_decimal():
	rollup = compute_rollup([_stat(1, avg_eapm=60), _stat(2, avg_eapm=64)], [])
	assert "**62** median" in _line_with(scouting_report.render(rollup), "eAPM")


def test_the_apm_line_is_omitted_when_no_game_carried_an_eapm():
	rollup = compute_rollup([_stat(1, avg_eapm=None)], [])
	assert "eAPM" not in (scouting_report.render(rollup) or "")


# ── the three splits ─────────────────────────────────────────────────────

def _split_fixture(strategy_games, spawn_games, unit_games):
	"""Rows giving each block exactly one key, at exactly the game count asked
	for. Every game is resolved, and the player wins the first of each."""
	total = max(strategy_games, spawn_games, unit_games)
	stats, labels = [], []
	for i in range(1, total + 1):
		stats.append(_stat(i, winner=(i == 1), units=("Knight",) if i <= unit_games else ("Militia",)))
		if i <= strategy_games:
			labels.append(_label(i, "archer_rush", "strategy"))
		if i <= spawn_games:
			labels.append(_label(i, "spawn_near_enemy", "spawn"))
	return compute_rollup(stats, labels)


def test_every_split_line_renders_with_its_win_loss_record():
	rendered = scouting_report.render(_rich())

	assert "Top strategy: **Archer Rush** · 5W-1L" in rendered
	assert "Top spawn: **Near Enemy** · 5W-2L" in rendered
	assert "Top unit: **Knight** · 5W-3L" in rendered


def test_only_the_top_key_of_each_block_reaches_the_reader():
	""" compute_rollup ranks by games descending; the report is the top line,
	not the whole list. Both keys here clear the floor, so the second one is
	dropped by the renderer rather than by the aggregation. """
	stats = [_stat(i, winner=True) for i in range(1, 13)]
	labels = [_label(i, "archer_rush" if i <= 7 else "scout_rush", "strategy") for i in range(1, 13)]
	rollup = compute_rollup(stats, labels)
	assert [s["key"] for s in rollup["strategies"]] == ["archer_rush", "scout_rush"]

	rendered = scouting_report.render(rollup)
	assert "Archer Rush" in rendered and "Scout Rush" not in rendered


def test_a_thin_strategy_is_dropped_while_the_other_two_lines_stay():
	rendered = scouting_report.render(_split_fixture(
		strategy_games=SPLIT_MIN_GAMES - 1, spawn_games=SPLIT_MIN_GAMES, unit_games=SPLIT_MIN_GAMES))

	assert "Top strategy" not in rendered
	assert "Top spawn" in rendered and "Top unit" in rendered


def test_a_thin_spawn_is_dropped_while_the_other_two_lines_stay():
	rendered = scouting_report.render(_split_fixture(
		strategy_games=SPLIT_MIN_GAMES, spawn_games=SPLIT_MIN_GAMES - 1, unit_games=SPLIT_MIN_GAMES))

	assert "Top spawn" not in rendered
	assert "Top strategy" in rendered and "Top unit" in rendered


def test_a_thin_unit_is_dropped_while_the_other_two_lines_stay():
	rendered = scouting_report.render(_split_fixture(
		strategy_games=SPLIT_MIN_GAMES, spawn_games=SPLIT_MIN_GAMES, unit_games=SPLIT_MIN_GAMES - 1))

	assert "Top unit" not in rendered
	assert "Top strategy" in rendered and "Top spawn" in rendered


def test_a_split_at_exactly_the_floor_still_renders():
	""" The floor is a minimum, not a strict one -- a test that only ever asked
	about 4 and 6 games would pass against either. """
	rendered = scouting_report.render(_split_fixture(
		strategy_games=SPLIT_MIN_GAMES, spawn_games=0, unit_games=0))
	assert "Top strategy" in rendered


def test_a_dropped_split_is_dropped_silently_with_no_low_sample_warning():
	""" A number the reader has to discount is worse than no number, and every
	hedge this product ever shipped was eventually read as a fact anyway. """
	rendered = scouting_report.render(_split_fixture(
		strategy_games=2, spawn_games=SPLIT_MIN_GAMES, unit_games=SPLIT_MIN_GAMES))

	lowered = rendered.lower()
	for hedge in ("only", "few", "small sample", "so far", "just", "low"):
		assert hedge not in lowered


def test_a_split_smaller_than_the_players_game_count_renders_without_complaint():
	""" Unresolved outcomes are excluded from every split but still count as
	games played, so a split's `games` is legitimately smaller than the row's.
	Nothing here may try to reconcile them. """
	stats = [_stat(i, winner=(True if i <= 5 else (False if i <= 8 else None))) for i in range(1, 13)]
	labels = [_label(i, "archer_rush", "strategy") for i in range(1, 13)]
	rollup = compute_rollup(stats, labels)

	assert rollup["strategies"][0]["games"] == 8, "4 unresolved games are out of the split"
	rendered = scouting_report.render(rollup)
	# 5W-3L is 8 games; the player played 12, and the split line never says so.
	strategy = _line_with(rendered, "Top strategy")
	assert strategy == "Top strategy: **Archer Rush** · 5W-3L"
	assert "12" not in strategy


def test_a_stored_label_key_renders_as_a_readable_name():
	rollup = _split_fixture(strategy_games=SPLIT_MIN_GAMES, spawn_games=SPLIT_MIN_GAMES, unit_games=0)

	rendered = scouting_report.render(rollup)
	assert "archer_rush" not in rendered and "**Archer Rush**" in rendered
	# The line already says "spawn"; the stored spawn_ prefix would say it twice.
	assert "Top spawn: **Near Enemy**" in rendered


# ── translation ──────────────────────────────────────────────────────────

def test_every_rendered_line_goes_through_the_callers_translator():
	""" Same convention as every other user-facing string in these files. """
	seen = []

	def gt(text):
		seen.append(text)
		return text

	rendered = scouting_report.render(_rich(), gt)
	assert len(seen) == len(_lines(rendered)) == 5
	assert all("{" in s for s in seen), "the translator sees whole sentences, not fragments"


# ── the player_rollups readers ───────────────────────────────────────────

class _FakeDb:
	def __init__(self, rows=()):
		self.rows = [dict(r) for r in rows]

	async def select_one(self, columns, table, where=None):
		assert table == "player_rollups"
		for row in self.rows:
			if all(row.get(k) == v for k, v in (where or {}).items()):
				return {c: row.get(c) for c in columns}
		return None

	async def select(self, columns, table, where=None, **_kwargs):
		assert table == "player_rollups"
		return [{c: row.get(c) for c in columns} for row in self.rows
		        if all(row.get(k) == v for k, v in (where or {}).items())]


def _stored(community_id, user_id, blob):
	return dict(community_id=community_id, user_id=user_id, games=12,
	            rollup=json.dumps(blob, sort_keys=True), computed_at=1000)


def test_fetch_returns_none_for_a_player_with_no_row(monkeypatch):
	""" None, never an empty blob: the absence IS the signal, and an empty
	blob would render as a report with every line missing instead of as
	"Statistics pending linking". """
	monkeypatch.setattr(rollups, "db", _FakeDb([_stored(1, 42, _blob())]))

	assert asyncio.run(rollups.fetch(1, 999)) is None
	assert asyncio.run(rollups.fetch(2, 42)) is None, "and a rollup in another community is not this one's"


def test_fetch_decodes_the_stored_json_blob(monkeypatch):
	monkeypatch.setattr(rollups, "db", _FakeDb([_stored(1, 42, _rich())]))

	assert asyncio.run(rollups.fetch(1, 42)) == _rich()


def test_fetch_community_maps_every_user_to_their_blob(monkeypatch):
	monkeypatch.setattr(rollups, "db", _FakeDb([
		_stored(1, 42, _rich()), _stored(1, 43, _blob()), _stored(2, 44, _rich())]))

	fetched = asyncio.run(rollups.fetch_community(1))
	assert set(fetched) == {42, 43}
	assert fetched[42]["medal_rates"]["games_ranked"] == 10


# ── /identity status' gated-features list ────────────────────────────────

def test_gaps_counts_the_players_with_no_rollup_against_the_window():
	gaps = scouting_report.gaps({42: _rich()}, {42, 43, 44})

	assert dict(feature="report", gated=2, of=3) in gaps


def test_gaps_counts_each_thin_split_over_the_scouted_players_only():
	""" The split lines are gated for players who HAVE a report; counting them
	against everyone seen would make one embed disagree with itself. """
	no_spawn = _split_fixture(strategy_games=SPLIT_MIN_GAMES, spawn_games=0, unit_games=SPLIT_MIN_GAMES)
	gaps = scouting_report.gaps({42: _rich(), 43: no_spawn}, {42, 43, 44})

	assert dict(feature="report", gated=1, of=3) in gaps
	assert dict(feature="spawn", gated=1, of=2) in gaps
	assert not [g for g in gaps if g["feature"] in ("strategy", "unit")]


def test_gaps_reports_nothing_when_every_line_is_filled():
	""" A community where nothing is gated must be told that, which is exactly
	what a hardcoded list of features could never say. """
	assert scouting_report.gaps({42: _rich(), 43: _rich()}, {42, 43}) == []


def test_gaps_ignores_a_rollup_for_someone_outside_the_window():
	""" A player who has not appeared in 90 days is not a gap anybody can
	chase, and counting them would make the gated numbers disagree with the
	coverage line above them. """
	assert scouting_report.gaps({42: _rich(), 99: _blob()}, {42}) == []


def test_gaps_on_a_community_with_nothing_stored_gates_every_player():
	assert scouting_report.gaps({}, {1, 2}) == [dict(feature="report", gated=2, of=2)]


# ── the /rank wiring (bot/commands/stats.py) ─────────────────────────────
# stats.py does `from nextcord import Member, Embed, Colour, File` and
# `import bot`; under CI none of that resolves, and importing it normally
# would first run bot/commands/__init__.py and star-import every other command
# module. Same trick as tests/test_identity.py: register minimal nextcord
# fakes and load the file by path.

def _load_stats_module(monkeypatch):
	fake_nextcord = types.ModuleType("nextcord")
	fake_nextcord.Member = object
	fake_nextcord.Colour = lambda value=0: value
	fake_nextcord.File = object

	class _FakeEmbed:
		def __init__(self, title=None, description=None, colour=None, color=None):
			self.title, self.description = title, description
			self.colour = colour if colour is not None else color
			self.fields = []

		def add_field(self, name=None, value=None, inline=True):
			self.fields.append(dict(name=name, value=value, inline=inline))

	fake_nextcord.Embed = _FakeEmbed
	monkeypatch.setitem(sys.modules, "nextcord", fake_nextcord)

	fake_nextcord_utils = types.ModuleType("nextcord.utils")
	fake_nextcord_utils.get = lambda *a, **k: None
	fake_nextcord_utils.find = lambda *a, **k: None
	fake_nextcord_utils.escape_markdown = lambda s: s
	monkeypatch.setitem(sys.modules, "nextcord.utils", fake_nextcord_utils)

	path = _REPO_ROOT / "bot" / "commands" / "stats.py"
	spec = importlib.util.spec_from_file_location("stats_standalone_test", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class _FakeCtx:
	def __init__(self, channel_id=4242):
		self.qc = types.SimpleNamespace(gt=lambda s: s, id=channel_id)
		self.channel = types.SimpleNamespace(id=channel_id)


def _wire(monkeypatch, community_id, rollup):
	asked = []

	async def _community_for_channel(channel_id):
		asked.append(channel_id)
		return community_id

	async def _fetch(cid, user_id):
		asked.append((cid, user_id))
		return rollup

	monkeypatch.setattr(community, "community_for_channel", _community_for_channel)
	monkeypatch.setattr(rollups, "fetch", _fetch)
	return asked


def test_rank_renders_the_pending_string_for_a_player_with_no_rollup(monkeypatch):
	stats = _load_stats_module(monkeypatch)
	asked = _wire(monkeypatch, community_id=7, rollup=None)

	value = asyncio.run(stats._scouting_report(_FakeCtx(channel_id=4242), 42))

	assert value == "Statistics pending linking"
	assert asked == [4242, (7, 42)], "the rollup is read for the channel's community"


def test_rank_renders_the_measured_report_for_a_scouted_player(monkeypatch):
	stats = _load_stats_module(monkeypatch)
	_wire(monkeypatch, community_id=7, rollup=_rich())

	value = asyncio.run(stats._scouting_report(_FakeCtx(), 42))

	assert value == scouting_report.render(_rich())
	assert "Statistics pending linking" not in value


def test_rank_says_nothing_at_all_on_a_channel_with_no_community(monkeypatch):
	""" An unenrolled channel is the ordinary state for most channels, not a
	linking gap: nothing was ever measured there and nothing is pending. """
	stats = _load_stats_module(monkeypatch)
	asked = _wire(monkeypatch, community_id=None, rollup=_rich())

	assert asyncio.run(stats._scouting_report(_FakeCtx(), 42)) is None
	assert asked == [4242], "and no rollup is read"


# ── what stage 5a removed ────────────────────────────────────────────────
# Source-level, because bot/web.py cannot be imported under CI (aiohttp.web +
# core.client's nextcord) -- the same approach tests/test_web_identity.py
# takes. These pin the deletion half of the cutover: the generated persona and
# scout read are gone from /rank, and nothing recomputes a persona on ingest.

def _source(*parts):
	return (_REPO_ROOT.joinpath(*parts)).read_text()


def test_rank_no_longer_renders_a_generated_persona_or_scout_read():
	""" Parsed rather than grepped: the persona stack has to be gone from the
	CODE, while the comments explaining what replaced it are exactly what a
	future reader needs. """
	tree = ast.parse(_source("bot", "commands", "stats.py"))
	strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
	names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
	names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

	for gone in ("persona", "scout_report", "tagline", "epithet", "parsed_matches"):
		assert gone not in strings, f"{gone} is still read out of a snapshot"
	assert "player_overview_snapshot" not in names
	assert not [s for s in strings if "Recurring tags" in s]
	assert "re" not in names, "the tag-stripping regex went with the prose it scrubbed"


def test_the_dashboard_overview_snapshot_is_gone_from_the_web_layer():
	assert "player_overview_snapshot" not in _source("bot", "web.py")


def test_the_ingest_path_no_longer_refreshes_personas():
	""" rs_player_personas stops being written here. The module itself stays
	until stage 6 drops the table, so this asserts the CALL is gone rather
	than the file. """
	src = _source("bot", "replay_stats", "store.py")
	tree = ast.parse(src)
	calls = [n for n in ast.walk(tree)
	         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
	         and n.func.attr == "refresh_match_users"]
	assert calls == []
	assert "import persona_store" not in src


def test_rank_still_builds_its_scouting_field_from_the_rollup_helper():
	tree = ast.parse(_source("bot", "commands", "stats.py"))
	profile = next(n for n in ast.walk(tree)
	               if isinstance(n, ast.AsyncFunctionDef) and n.name == "_rank_profile")
	called = {n.func.id for n in ast.walk(profile)
	          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
	assert "_scouting_report" in called
