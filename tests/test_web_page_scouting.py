"""The SPA's scouting-report render functions, EXECUTED.

WHY THIS FILE EXISTS. bot/web_page.html is a self-contained SPA: 200KB of
inline JS that renders everything bot/web.py's REST API returns. The scouting
block (`scoutingLines`, `scoutingCard`, `scoutingSpawnText`) is the last mile of
stage 5's honest-numbers contract -- every figure carries its own denominator, a
missing measurement is omitted rather than zeroed, and a linked player with a
rollup row never sees "Statistics pending linking". The only test covering any
of it was a substring grep for field names that must NOT appear.

That is not coverage, and four mutations proved it by shipping green:

  * the peak rendered as `peak undefined over undefined`
  * medal rates normalised to sum to 100%, turning an honest 34% + 18% = 52%
    into 65%/35% -- the exact lie the denominator exists to prevent
  * a per-split `(low sample)` caveat the contract explicitly forbids, because
    compute_rollup has ALREADY dropped everything under the floor
  * the third-state card rewritten to tell a fully linked player with a rollup
    row that their statistics are "pending linking"

The server-side halves of all four are caught (breaking scouting_report.PENDING
fails six tests). Only the render was unguarded.

HOW. The functions are pure -- data in, HTML string out -- so they are pulled
out of the page by name and run under node, and every assertion lives here in
Python where pytest can report it. Extracting them into a separate .js file
would mean the SPA stops being self-contained and bot/web.py grows a second
route to serve; reading them out of the page instead keeps one shipped artifact
and still executes the real code. If a function is renamed or deleted, the
extraction fails loudly rather than quietly covering nothing.

`node` is REQUIRED, not optional. A skip would put this file straight back
where it started -- reporting green while asserting nothing -- so a missing
runtime fails. CI installs it explicitly (.github/workflows/ci.yml).
"""
import json
import os
import shutil
import subprocess
import textwrap

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PAGE = os.path.join(_REPO_ROOT, "bot", "web_page.html")

# The functions under test, plus the one page helper they call. `esc` is NOT
# extracted: it reaches for `document` to escape via the DOM, and the harness
# below supplies an equivalent instead. Nothing asserted here depends on the
# exact escaping, only on what reaches it.
_FUNCTIONS = ("scoutingLines", "scoutingCard", "scoutingSpawnText", "htmlCard")


def _page_source():
	with open(_PAGE, encoding="utf-8") as fh:
		return fh.read()


def _extract_function(source, name):
	""" One `function name(...) {...}` block, brace-matched.

	String literals and both comment forms are skipped so a brace inside them
	cannot end the block early. Regex literals are NOT handled -- none of these
	functions contains one, and if that changes the extraction raises here
	rather than returning something subtly wrong.
	"""
	start = source.find(f"function {name}(")
	assert start != -1, f"bot/web_page.html no longer defines {name}() — coverage would be silently lost"
	i = source.index("{", start)
	depth, quote, k = 0, None, i
	while k < len(source):
		ch = source[k]
		nxt = source[k + 1] if k + 1 < len(source) else ""
		if quote:
			if ch == "\\":
				k += 2
				continue
			if ch == quote:
				quote = None
		elif ch in "'\"`":
			quote = ch
		elif ch == "/" and nxt == "/":
			k = source.find("\n", k)
			if k == -1:
				break
			continue
		elif ch == "/" and nxt == "*":
			k = source.index("*/", k) + 2
			continue
		elif ch == "{":
			depth += 1
		elif ch == "}":
			depth -= 1
			if depth == 0:
				return source[start:k + 1]
		k += 1
	raise AssertionError(f"unbalanced braces while extracting {name}() from bot/web_page.html")


def _node():
	node = shutil.which("node")
	assert node, (
		"node is required to execute bot/web_page.html's render functions. This test "
		"must never be skipped: skipping it restores the exact hole it was written to "
		"close — three of stage 5's four honest-number rules rest on nothing else.")
	return node


_HARNESS = """
function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
const CASES = %s;
const out = {};
for (const key of Object.keys(CASES)) {
  const rep = CASES[key];
  out[key] = {
    lines: scoutingLines(rep),
    card: scoutingCard(rep),
    spawn: scoutingSpawnText(rep),
  };
}
process.stdout.write(JSON.stringify(out));
"""


def _render(tmp_path, cases):
	""" {case name: report payload} -> {case name: {lines, card, spawn}}. """
	source = _page_source()
	script = "\n".join(_extract_function(source, name) for name in _FUNCTIONS)
	script += _HARNESS % json.dumps(cases)
	path = tmp_path / "scouting_harness.js"
	path.write_text(script, encoding="utf-8")

	proc = subprocess.run([_node(), str(path)], capture_output=True, text=True, timeout=60)
	assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
	return json.loads(proc.stdout)


# ── payloads, shaped exactly as bot/web.py's _scouting_report_payload emits ──
# 34% military and 18% villager sum to 52%, which is CORRECT: the two medals are
# independent and a player earns neither in most games. That is the number the
# denominator exists to make legible, and the number a "normalise to 100%" bug
# destroys.

FULL = {
	"pending": None,
	"medals": {"military_pct": 34, "villager_pct": 18, "games_ranked": 50},
	"apm": {"median_avg": 62.5, "games_avg": 38},
	"strategies": [{"key": "archer_rush", "label": "Archer Rush", "games": 12, "wins": 7,
	                "losses": 5, "winrate": 58.3},
	               {"key": "knight_rush", "label": "Knight Rush", "games": 6, "wins": 2,
	                "losses": 4, "winrate": 33.3}],
	"spawns": [{"key": "spawn_near_enemy", "label": "Near Enemy", "games": 9, "wins": 4,
	            "losses": 5, "winrate": 44.4},
	           {"key": "spawn_isolated", "label": "Isolated", "games": 7, "wins": 5,
	            "losses": 2, "winrate": 71.4}],
	"units": [{"key": "Crossbowman", "label": "Crossbowman", "games": 11, "wins": 6,
	           "losses": 5, "winrate": 54.5}],
}

WITH_PEAK = dict(FULL, apm={"median_avg": 62.5, "games_avg": 38,
                            "median_peak": 140, "games_peak": 9})

# A linked player with a row and nothing above any floor. `pending` is null:
# they ARE linked, so the pending sentence would be a lie about them.
THIN = {"pending": None, "medals": None, "apm": None,
        "strategies": [], "spawns": [], "units": []}

UNLINKED = {"pending": "Statistics pending linking"}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
	return _render(tmp_path_factory.mktemp("scouting"),
	               {"full": FULL, "with_peak": WITH_PEAK, "thin": THIN, "unlinked": UNLINKED})


# ── the peak, which is absent on every production row today ──────────────

def test_the_apm_line_omits_the_peak_entirely_when_none_was_captured(rendered):
	""" peak_eapm is NULL on every production row, so bot/web.py leaves the peak
	keys OUT of the payload rather than sending nulls. Reading them anyway
	renders `peak undefined over undefined`, which is what shipped green. """
	apm = [ln for ln in rendered["full"]["lines"] if ln.startswith("eAPM")]

	assert apm == ["eAPM: 62.5 median over 38 games"]
	assert "undefined" not in json.dumps(rendered["full"])
	assert "peak" not in apm[0].lower()
	assert "—" not in apm[0] and "NaN" not in apm[0] and "null" not in apm[0]


def test_the_peak_renders_with_its_own_count_once_buckets_arrive(rendered):
	""" Two independent samples, never one shared figure: 38 games have an
	average, 9 have a peak, and the line says both. """
	apm = [ln for ln in rendered["with_peak"]["lines"] if ln.startswith("eAPM")]

	assert apm == ["eAPM: 62.5 median over 38 games · peak 140 over 9"]


# ── medal rates and their denominator ────────────────────────────────────

def test_medal_rates_render_exactly_as_measured_and_are_never_normalised(rendered):
	""" 34 + 18 = 52, not 100. The two medals are independent, most games earn
	neither, and rescaling them to fill a pie turns a true 34% into a false 65%.
	The denominator beside them is what makes the 52% legible. """
	medals = [ln for ln in rendered["full"]["lines"] if ln.startswith("Medals")]

	assert medals == ["Medals: 34% military · 18% villager, over 50 ranked games"]
	assert "65%" not in medals[0] and "35%" not in medals[0]


def test_the_medal_line_always_carries_its_ranked_denominator(rendered):
	medals = [ln for ln in rendered["full"]["lines"] if ln.startswith("Medals")][0]

	assert "over 50 ranked games" in medals


def test_a_report_with_no_measured_block_renders_no_line_for_it(rendered):
	""" Each block is independent: a missing one is omitted, never zeroed. """
	assert rendered["thin"]["lines"] == []


# ── the splits, already floored upstream ─────────────────────────────────

def test_each_split_renders_its_top_key_with_a_win_loss_record(rendered):
	lines = rendered["full"]["lines"]

	assert "Top strategy: Archer Rush · 7W-5L" in lines
	assert "Top spawn: Near Enemy · 4W-5L" in lines
	assert "Top unit: Crossbowman · 6W-5L" in lines
	assert not [ln for ln in lines if "Knight Rush" in ln], "only the top key reaches the reader"


def test_no_split_line_carries_a_low_sample_caveat(rendered):
	""" compute_rollup drops a split below SPLIT_MIN_GAMES from the blob
	entirely, so there is nothing left to hedge about and a hedge here would be
	pure invention. Every hedge this product ever shipped was eventually read as
	a fact anyway. """
	blob = json.dumps(rendered).lower()

	for hedge in ("low sample", "small sample", "few games", "(low", "provisional",
	              "unreliable", "approx", "so far"):
		assert hedge not in blob, f"the SPA hedges a floored split with {hedge!r}"


def test_the_spawn_read_lists_the_measured_splits_and_nothing_else(rendered):
	assert rendered["full"]["spawn"] == "Near Enemy 4W-5L; Isolated 5W-2L."
	assert rendered["thin"]["spawn"] == "No spawn split above the sample floor yet."
	assert rendered["unlinked"]["spawn"] == "Statistics pending linking"


# ── the three states, which are three different statements ───────────────

def test_an_unlinked_player_gets_the_pending_sentence_and_nothing_else(rendered):
	card = rendered["unlinked"]["card"]

	assert "Statistics pending linking" in card
	assert rendered["unlinked"]["lines"] == []
	# No report of zeros beside the notice: a row of zeros would be a
	# measurement nobody took. (The card's own sub-label says "link an AoE2
	# profile", so the check is for reported figures, not for every digit.)
	for reported in ("%", "W-", "ranked games", "median", "eAPM", "Top "):
		assert reported not in card, f"the pending card also reports {reported!r}"


def test_a_linked_player_with_a_rollup_is_never_told_their_stats_are_pending(rendered):
	""" The mutation that shipped green: the third-state card rewritten to show
	"Statistics pending linking" to a fully linked player who HAS a row. It is
	false about them, and it is the one sentence this state exists to avoid. """
	for case in ("full", "with_peak", "thin"):
		assert "pending linking" not in rendered[case]["card"].lower(), \
			f"the {case} player is linked and has a row"
		assert "pending linking" not in rendered[case]["spawn"].lower()


def test_a_linked_player_with_nothing_above_the_floor_is_told_exactly_that(rendered):
	""" Not pending (they are linked), not zeros (nobody measured it), not an
	empty card. """
	card = rendered["thin"]["card"]

	assert "Nothing above the sample floor yet" in card
	assert "0%" not in card and "0W-0L" not in card


def test_the_card_renders_every_measured_line_it_was_given(rendered):
	card = rendered["full"]["card"]

	for line in rendered["full"]["lines"]:
		assert line in card, f"scoutingCard dropped {line!r}"
	assert "measured facts, with their samples" in card


def test_no_community_resolved_renders_nothing_at_all(tmp_path):
	""" `null` is not the same as `{pending: ...}`: a page with no community
	behind it measured nothing and has no linking gap to report. """
	rendered = _render(tmp_path, {"none": None})

	assert rendered["none"]["card"] == ""
	assert rendered["none"]["lines"] == []
	assert rendered["none"]["spawn"] == "No community stats configured."


# ── the harness itself ───────────────────────────────────────────────────

def test_the_extractor_pulls_whole_function_bodies(tmp_path):
	""" A meta-test, because a silently truncated extraction would make every
	assertion above run against a syntax error -- or worse, against less code
	than the page actually ships. """
	source = _page_source()
	for name in _FUNCTIONS:
		body = _extract_function(source, name)
		assert body.startswith(f"function {name}(")
		assert body.rstrip().endswith("}")
		assert body.count("{") == body.count("}") or "'" in body


def test_the_extracted_functions_are_the_ones_the_page_actually_calls():
	""" Coverage of a dead function is not coverage. Each name under test must
	be called somewhere in the page besides its own definition. """
	source = _page_source()
	for name in ("scoutingLines", "scoutingCard", "scoutingSpawnText"):
		calls = source.count(f"{name}(") - source.count(f"function {name}(")
		assert calls > 0, f"{name}() is defined but never called — the SPA no longer renders it"


def test_node_is_available_and_the_suite_never_skips_this_file():
	""" Stated as its own assertion so a CI image losing node fails here, with
	this message, rather than as four confusing render failures. """
	assert shutil.which("node"), textwrap.dedent("""
		node is not on PATH. These tests execute bot/web_page.html's real render
		functions; without a JS runtime the SPA's honest-number rules have no
		executable coverage at all. Install node (CI does this explicitly in
		.github/workflows/ci.yml) rather than skipping the file.""")
