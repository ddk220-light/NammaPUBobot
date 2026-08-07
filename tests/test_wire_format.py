"""Interaction custom_ids are a WIRE FORMAT. They must not change.

A custom_id is written into a Discord message and lives there for as long as
the message does. The bot does not own those strings once they are sent — a
quiz card posted this morning, a betting card on a match still being played,
an /insights card from before that command was removed. Every one of them will
hand its custom_id back to whatever process is running when somebody presses
the button, which may be a build from weeks later.

WHAT BREAKS, AND HOW QUIETLY. Change a prefix and the routers stop recognising
their own buttons. Every router in this bot is written to fall through on an
unrecognised id — it has to be, because they all share one global
on_interaction chain and each must ignore the others' presses. So the failure
is not an exception: the press is simply never answered, Discord shows the user
"This interaction failed" after three seconds, and the logs say nothing at all.

That is why these are pinned as literal strings rather than derived from the
builders. Deriving both sides from the same constant would let a rename pass
this file untouched, which is precisely the change that must not pass.

The parsers are exercised through their real functions, so the test fails if
either half moves: the string a builder emits, or the prefix a router accepts.
"""
from nammaoe2bot.features.betting import scoring as bet_scoring
from nammaoe2bot.features.quiz import scoring as quiz_scoring


# ── the exact strings, written down ──────────────────────────────────────
# Each one is a real id shape currently in Discord message history. The
# post/match numbers are stand-ins; the STRUCTURE is what is pinned.
LIVE_CUSTOM_IDS = {
	"quiz answer":        "quiz:412:ans:2",
	"quiz multi-select":  "quiz:412:msel",
	"quiz reveal":        "quiz:412:reveal",
	"bet":                "bet:77:1:100",
	"bet cancel":         "betcancel:77",
	"insights full":      "insights:full:scout_rush:30",
}


class TestQuiz:
	def test_an_answer_press_parses_to_its_option_index(self):
		assert quiz_scoring.parse_custom_id(LIVE_CUSTOM_IDS["quiz answer"]) == ("answer", 412, 2)

	def test_a_multi_select_press_parses(self):
		kind, post_id, _ = quiz_scoring.parse_custom_id(LIVE_CUSTOM_IDS["quiz multi-select"])
		assert (kind, post_id) == ("mselect", 412)

	def test_the_reveal_button_still_parses(self):
		""" Nothing posts these any more — the reveal era ended when the quiz
		became a 24-hour public poll — but the single post that was open at
		deploy time still carries one, and pressing it re-renders that card
		into poll form. A parser that stopped recognising it would leave that
		card permanently dead. """
		assert quiz_scoring.parse_custom_id(LIVE_CUSTOM_IDS["quiz reveal"]) == ("reveal", 412, None)

	def test_a_foreign_id_falls_through_rather_than_raising(self):
		""" One global on_interaction chain, several routers. Each must ignore
		the others' presses without an exception, or a bet press would be
		answered by the quiz router. """
		for foreign in (LIVE_CUSTOM_IDS["bet"], LIVE_CUSTOM_IDS["insights full"], "", "nonsense"):
			assert quiz_scoring.parse_custom_id(foreign) is None


class TestBetting:
	def test_a_bet_press_parses_to_side_and_stake(self):
		assert bet_scoring.parse_bet_custom_id(LIVE_CUSTOM_IDS["bet"]) == (77, 1, 100)

	def test_a_stake_outside_the_published_tiers_is_refused(self):
		""" STAKES is the button set, and the parser checks membership rather
		than range — a hand-crafted custom_id claiming 100000 is refused
		before it reaches the gold ledger. """
		assert bet_scoring.parse_bet_custom_id("bet:77:1:100000") is None
		assert bet_scoring.parse_bet_custom_id("bet:77:2:100") is None, "side must be 0 or 1"

	def test_a_cancel_press_parses_to_its_post(self):
		assert bet_scoring.parse_cancel_custom_id(LIVE_CUSTOM_IDS["bet cancel"]) == 77

	def test_bet_and_betcancel_do_not_capture_each_other(self):
		""" THE COLON IS LOAD-BEARING. `betcancel:77` does not start with
		`bet:` — the character after "bet" is "c" — but it does start with
		"bet", so a prefix test written without the colon would route every
		cancel into the bet handler and take the presser's stake a second time
		instead of giving it back. """
		assert bet_scoring.parse_bet_custom_id(LIVE_CUSTOM_IDS["bet cancel"]) is None
		assert bet_scoring.parse_cancel_custom_id(LIVE_CUSTOM_IDS["bet"]) is None

	def test_a_foreign_id_falls_through_rather_than_raising(self):
		for foreign in (LIVE_CUSTOM_IDS["quiz answer"], LIVE_CUSTOM_IDS["insights full"], "", "x"):
			assert bet_scoring.parse_bet_custom_id(foreign) is None
			assert bet_scoring.parse_cancel_custom_id(foreign) is None


class TestTheBuildersStillEmitTheseShapes:
	""" The other half. The parsers above could keep accepting a format nobody
	writes any more; these pin the source strings in the modules that build the
	buttons, so a change to either side fails.

	Read as text rather than by calling the builders: building a real button
	needs nextcord's ui classes and a live post row, and the question here is
	only what f-string the module contains. """

	def _source(self, relative):
		import os
		root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
		with open(os.path.join(root, relative), encoding="utf-8") as f:
			return f.read()

	def test_the_betting_view_builds_bet_and_betcancel(self):
		src = self._source("nammaoe2bot/features/betting/embeds.py")
		assert 'f"bet:{post_id}:{side}:{stake}"' in src
		assert 'f"betcancel:{post_id}"' in src

	def test_the_quiz_view_builds_ans_and_msel(self):
		src = self._source("nammaoe2bot/features/quiz/embeds.py")
		assert 'f"quiz:{post_id}:ans:{i}"' in src
		assert 'f"quiz:{post_id}:msel"' in src

	def test_the_insights_router_still_accepts_its_prefix(self):
		""" /insights was removed in the command consolidation and nothing
		posts these buttons now — but the cards are in channel history and a
		press on one has to be answered. """
		src = self._source("nammaoe2bot/derived/classifications/interactions.py")
		assert '"insights:full:"' in src
