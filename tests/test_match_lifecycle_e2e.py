"""One whole match, driven through the real objects, with the real wiring.

WHAT THIS COVERS THAT NOTHING ELSE DOES. tests/test_match_lifecycle.py checks
the dispatcher in isolation and greps the source to confirm the domain imports
no feature. tests/test_draft_substitutions.py drives one event. Neither runs a
match from teams-formed to result-reported, and that whole sequence is what the
restructure rearranged: seven events emitted from four methods across two
files, answered by handlers in a third, in an order where two of the positions
are load-bearing.

The failure this is here to catch is an event that stops firing. Every handler
is isolated and every feature entry point swallows its own errors — correctly,
because none of them may decide whether a match reports — so a lifecycle event
that silently stopped being emitted produces no exception, no log line and no
failing unit test. It produces a betting card that never appears, or a payout
that never happens, discovered days later by a player asking where their gold
went. That is precisely how audience predictions died the first time: 3312
ranked matches, zero prediction_posts, an AttributeError swallowed by a
best-effort guard on every single one.

WHAT IS REAL HERE: Match, Draft, MatchLifecycle, and wire_match_lifecycle —
including the `ranked` and `predictions_enabled` guards each handler applies,
since those moved out of the domain and into wiring and could be wrong there.

WHAT IS FAKED: the Discord layer (ctx, members, channel), the queue and queue
channel, and the FOUR OUTERMOST feature entry points — betting's
open/restart/resolve/void, the lobby watcher, the storyline builders and civ
recording. Faking those is the point rather than a compromise: what is under
test is that the chain reaches them, in order, with the right match. What each
one then does has its own tests.

No pytest-asyncio in this repo, so every coroutine is driven with asyncio.run().
"""
import asyncio
import types

import nammaoe2bot.features.betting as betting
import nammaoe2bot.features.civs.matcher as civ_matcher
import nammaoe2bot.features.storylines.insights as insights
import nammaoe2bot.features.storylines.payoff as payoff
import nammaoe2bot.pickup.stats as stats
import nammaoe2bot.wiring as wiring
from nammaoe2bot.app import Application
from nammaoe2bot.features.lobby import watcher as lobby_watcher
from nammaoe2bot.pickup.match.match import Match


# ── the Discord layer, faked ─────────────────────────────────────────────
class FakeMember:
	def __init__(self, uid):
		self.id = uid
		self.name = f"p{uid}"
		self.nick = None
		self.mention = f"<@{uid}>"
		self.roles = []
		# embeds.py checks `isinstance(p.activity, Streaming)` to put a twitch
		# link on the card. None is what a member who is not streaming has.
		self.activity = None


class FakeCtx:
	def __init__(self, qc):
		self.qc = qc
		self.channel = types.SimpleNamespace(id=900, name="pickups")
		self.sent = []

	async def notice(self, content=None, embed=None):
		self.sent.append(embed if content is None else content)

	async def success(self, content=None, embed=None):
		self.sent.append(content)


class FakeQueue:
	def __init__(self, qc):
		self.qc = qc
		self.name = "namma_nomad"
		self.id = 1
		self.last_maps = []
		self.cfg = types.SimpleNamespace(map_cooldown=1, p_key=1)
		self.queue = []

	async def revert(self, *_a, **_k):
		return None


class FakeQueueChannel:
	def __init__(self, app):
		self.id = 900
		self.app = app
		# A rating system: register_match_ranked rates the two teams and writes
		# rating_history against `rating.channel_id`, which is the channel a
		# queue borrows its ratings FROM (usually itself).
		self.rating = types.SimpleNamespace(
			get_players=self._get_players, channel_id=900, rate=self._rate)
		# What pickup/match/embeds.py reads off the channel config while
		# rendering the teams card. Both off: the card then renders plain
		# names, which keeps the embed out of the way of what is under test.
		self.cfg = types.SimpleNamespace(emoji_ranks=False, rating_nicks=False)

	def rating_rank(self, _rating):
		return {"rank": "", "role": None}

	def gt(self, text):
		return text

	async def _get_players(self, user_ids):
		return [dict(user_id=uid, rating=1500, deviation=100, wins=0, losses=0, draws=0,
					 streak=0, is_hidden=0) for uid in user_ids]

	def _rate(self, winners, losers, draw=False):
		"""+10/-10, returned as the (winners, losers) PAIR every real rating
		system returns — stats.py unpacks `results[-1][0]` and `[1]` separately.
		The arithmetic is a stand-in; the shape is not. A real Glicko2
		calculation here would only add a way for this file to fail for a
		reason that has nothing to do with the lifecycle."""
		delta = 0 if draw else 10
		won = [{**p, "rating": p["rating"] + delta,
				"wins": p.get("wins", 0) + (0 if draw else 1)} for p in winners]
		lost = [{**p, "rating": p["rating"] - delta,
				 "losses": p.get("losses", 0) + (0 if draw else 1)} for p in losers]
		return won, lost

	async def update_rating_roles(self, *_members):
		return None

	async def remove_members(self, *_members, **_kw):
		return None


def go_live(match, ctx):
	"""What Match.next_state does on the transition into WAITING_REPORT: set
	the state, then run start_waiting_report. Driven by hand because next_state
	pops from a queue of states this test does not need to model — but the
	state itself matters, since Draft.sub_for gates on it."""
	match.state = Match.WAITING_REPORT
	return match.start_waiting_report(ctx)


def build_match(app, ranked=True, predictions_enabled=True):
	"""A real Match, constructed the way Match.new does but without the DB
	round-trips for the id and the ratings."""
	qc = FakeQueueChannel(app)
	queue = FakeQueue(qc)
	players = [FakeMember(i) for i in range(1, 5)]
	ratings = {p.id: 1500 for p in players}
	match = Match(
		1390600, queue, qc, players, ratings,
		ranked=ranked, predictions_enabled=predictions_enabled,
		pick_teams="matchmaking", team_size=2, match_lifetime=3600,
	)
	match.init_teams("matchmaking")
	match.maps = ["Arabia"]
	app.active_matches.append(match)
	return match


class Recorder:
	"""Swaps every outermost feature entry point for a call log, and returns
	the ordered list of (event, match_id) the chain actually delivered."""

	def __init__(self, monkeypatch):
		self.calls = []
		m = monkeypatch

		def hook(label, result=None):
			async def _fn(*args, **_kw):
				match_or_id = args[0] if args else None
				mid = getattr(match_or_id, "id", match_or_id)
				self.calls.append((label, mid))
				return result
			return _fn

		# betting: the package attributes wiring resolves at call time
		m.setattr(betting, "open_for_match", hook("betting.open"))
		m.setattr(betting, "restart_for_match", hook("betting.restart"))
		m.setattr(betting, "resolve_for_match", hook("betting.settle"))
		m.setattr(betting, "void_for_match", hook("betting.void"))
		# the lobby watcher — start_for is sync, stop_for is async
		def _start_for(match, _channel):
			self.calls.append(("lobby.start", match.id))
		m.setattr(lobby_watcher, "start_for", _start_for)
		m.setattr(lobby_watcher, "stop_for", hook("lobby.stop"))
		# storylines and civ recording
		m.setattr(insights, "build_insights_embed", hook("storyline.tease"))
		m.setattr(payoff, "build_payoff_embed", hook("storyline.payoff"))
		m.setattr(civ_matcher, "schedule", lambda *a, **k: self.calls.append(("civs.record", a[1])))
		# THE RESULT WRITE IS NOT STUBBED, and that is deliberate. It is
		# register_match_* that emits `result_recorded`, so replacing it would
		# delete the event whose position this file exists to check — the test
		# would then be asserting an order it had constructed itself. The real
		# function runs; only the DATABASE under it is fake.
		fake_db = _RecordingDb(self.calls)
		m.setattr(stats, "db", fake_db)

	@property
	def labels(self):
		return [label for label, _mid in self.calls]


class _RecordingDb:
	"""Enough of the adapter for stats.register_match_* to run for real.

	Only the `matches` INSERT is logged — that row is what
	store.unsettled_books JOINs on, so its position relative to settlement is
	the whole point. The rest (player_ratings, match_players, rating_history)
	are accepted silently; they have their own tests and logging them would
	bury the sequence under bookkeeping."""

	def __init__(self, sink):
		self._sink = sink

	async def insert(self, table, row, **_kw):
		if table == "matches":
			self._sink.append(("stats.register", row["match_id"]))
		return 1

	async def insert_many(self, *_a, **_k):
		return None

	async def update(self, *_a, **_k):
		return None

	async def select_one(self, *_a, **_k):
		return None

	async def select(self, *_a, **_k):
		return []


def wired_app():
	app = Application(client=None)
	wiring.wire_match_lifecycle(app)
	return app


# ── the whole sequence ───────────────────────────────────────────────────
class TestAFullRankedMatch:
	def test_every_feature_fires_once_in_the_right_order(self, monkeypatch):
		""" The sequence a real match produces, start to finish. Read it as the
		specification: this is what a completed ranked pickup does. """
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app)
		ctx = FakeCtx(match.qc)

		asyncio.run(go_live(match, ctx))   # teams final, match live
		match.winner = 0
		asyncio.run(match.finish_match(ctx))           # result reported

		assert rec.labels == [
			"storyline.tease",     # teams_posted, from final_message
			"lobby.start",         # live
			"betting.open",        # live
			"lobby.stop",          # ending
			"stats.register",      # the `matches` row
			"civs.record",         # result_recorded
			"storyline.payoff",    # finished
			"betting.settle",      # finished
		]
		assert {mid for _label, mid in rec.calls} == {match.id}

	def test_settlement_happens_after_the_result_is_written(self, monkeypatch):
		""" THE ORDER THAT CARRIES MONEY. store.unsettled_books finds a stranded
		book by JOINing prediction_posts to the `matches` row, so a payout that
		crashes half-way is only recoverable if that row already exists. If
		settle ever moves above register, a crash mid-payout becomes invisible
		to the resume sweep and the gold is simply gone. """
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app)
		ctx = FakeCtx(match.qc)
		asyncio.run(go_live(match, ctx))
		match.winner = 0
		asyncio.run(match.finish_match(ctx))

		assert rec.labels.index("stats.register") < rec.labels.index("betting.settle")

	def test_the_watcher_stops_before_the_result_is_written(self, monkeypatch):
		""" `ending` exists as a separate event from `finished` only because the
		lobby teardown ran on the near side of the result write before this
		indirection existed. Collapsing the two would move it. """
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app)
		ctx = FakeCtx(match.qc)
		asyncio.run(go_live(match, ctx))
		asyncio.run(match.finish_match(ctx))

		assert rec.labels.index("lobby.stop") < rec.labels.index("stats.register")

	def test_the_match_leaves_active_matches_before_anything_is_announced(self, monkeypatch):
		""" finish_match drops the match on its FIRST line. Betting captures
		is_player at press time precisely because the roster is gone from
		app.active_matches by the time settlement runs. """
		seen = []
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app)
		ctx = FakeCtx(match.qc)
		asyncio.run(go_live(match, ctx))

		async def _watch(m, _c):
			seen.append(m in app.active_matches)
		app.match_events.on("ending", _watch)

		asyncio.run(match.finish_match(ctx))
		assert seen == [False], "the match was still live when 'ending' fired"
		assert rec.labels  # the rest of the chain still ran


class TestACancelledMatch:
	def test_the_book_is_voided_and_the_watcher_stopped(self, monkeypatch):
		""" Aborted: no result will ever come, so every stake goes back. The
		watcher stops FIRST — it should not be holding a match whose stakes are
		about to be handed back. """
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app)
		ctx = FakeCtx(match.qc)
		asyncio.run(go_live(match, ctx))
		rec.calls.clear()

		asyncio.run(match.cancel(ctx))
		assert rec.labels == ["lobby.stop", "betting.void"]

	def test_a_cancel_never_settles(self, monkeypatch):
		""" Paying out a match that produced no winner is the one thing a void
		exists to prevent. """
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app)
		ctx = FakeCtx(match.qc)
		asyncio.run(go_live(match, ctx))
		asyncio.run(match.cancel(ctx))
		assert "betting.settle" not in rec.labels
		assert "stats.register" not in rec.labels


class TestASubstitutionUnderALiveBook:
	def test_a_swap_restarts_the_book_and_the_match_still_completes(self, monkeypatch):
		""" The end-to-end version of the /subfor defect: a roster change has to
		refund and re-open, AND the match has to go on to settle normally
		afterwards. Testing the restart alone would miss a restart that left the
		match in a state it could not finish from. """
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app)
		ctx = FakeCtx(match.qc)
		asyncio.run(go_live(match, ctx))
		rec.calls.clear()

		sub_in = FakeMember(9)
		asyncio.run(match.draft.sub_for(ctx, match.teams[0][1], sub_in, force=True))
		assert rec.labels == ["betting.restart"]
		assert sub_in in match.players

		rec.calls.clear()
		match.winner = 0
		asyncio.run(match.finish_match(ctx))
		assert rec.labels == [
			"lobby.stop", "stats.register", "civs.record",
			"storyline.payoff", "betting.settle",
		]


class TestTheGuardsThatMovedIntoWiring:
	""" `ranked` and `predictions_enabled` used to be tested at the emit site
	inside Match. They are now inside the handlers, so a mistake there is a
	feature firing on a match it must not touch — an unranked kickabout opening
	a real betting book, say. """

	def test_an_unranked_match_opens_no_book_and_starts_no_watcher(self, monkeypatch):
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app, ranked=False)
		ctx = FakeCtx(match.qc)
		asyncio.run(go_live(match, ctx))
		asyncio.run(match.finish_match(ctx))

		assert "betting.open" not in rec.labels
		assert "lobby.start" not in rec.labels
		assert "betting.settle" not in rec.labels
		assert "storyline.payoff" not in rec.labels

	def test_an_unranked_match_still_records_its_result_and_civs(self, monkeypatch):
		""" The converse, so the test above cannot pass by everything being
		switched off. An unranked game is still a game that happened. """
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app, ranked=False)
		ctx = FakeCtx(match.qc)
		asyncio.run(match.finish_match(ctx))
		assert "stats.register" in rec.labels
		assert "civs.record" in rec.labels

	def test_a_queue_with_predictions_disabled_opens_no_book(self, monkeypatch):
		""" ...but everything else about the match is untouched. """
		rec = Recorder(monkeypatch)
		app = wired_app()
		match = build_match(app, predictions_enabled=False)
		ctx = FakeCtx(match.qc)
		asyncio.run(go_live(match, ctx))

		assert "betting.open" not in rec.labels
		assert "lobby.start" in rec.labels


class TestIsolation:
	def test_a_feature_that_raises_cannot_stop_the_match_reporting(self, monkeypatch):
		""" The property every one of these handlers depends on. A betting
		outage, a lobby API timeout or a storyline that cannot be computed must
		not prevent a result being recorded — the match is the product, the
		features are decoration. """
		rec = Recorder(monkeypatch)
		app = wired_app()

		async def _explode(*_a, **_k):
			raise RuntimeError("betting database is down")
		monkeypatch.setattr(betting, "open_for_match", _explode)
		monkeypatch.setattr(betting, "resolve_for_match", _explode)
		monkeypatch.setattr(insights, "build_insights_embed", _explode)

		match = build_match(app)
		ctx = FakeCtx(match.qc)
		asyncio.run(go_live(match, ctx))
		match.winner = 0
		asyncio.run(match.finish_match(ctx))       # must not raise

		assert "stats.register" in rec.labels, "the result was not recorded"
		assert "lobby.start" in rec.labels, "one feature's failure took out another"
		assert "civs.record" in rec.labels
