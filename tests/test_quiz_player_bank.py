"""nammaoe2bot/features/quiz/player_bank.py — player-source quiz questions generated live from
metric_boards, and the day-parity selection that decides when they are used.

There is no pytest-asyncio in this repo: an `async def test_` is silently
SKIPPED and reports as passing. Every async path here is driven from a sync
test with asyncio.run(...), against fakes — never a real database.
"""
import asyncio
import random

from nammaoe2bot.derived import boards as derived_boards
from nammaoe2bot.features.quiz import player_bank as pb
from nammaoe2bot.features.quiz import schedule as sched


def _leaders(n, direction="high", start=41.5, step=3.2, nicks=None):
    """`n` leaders already in the metric's WINNING order, the way
    compute_board hands them over: descending for a 'high' metric, ascending
    for a 'low' one. Every value is fractional and none is a round number: a
    leaked value has to be unmistakable ('41.5' cannot hide inside an Elo), and
    a whole number would render as '35' where the row reads 35.0."""
    return [
        dict(user_id=1000 + i,
             nick=(nicks[i] if nicks else f"p{i}"),
             avg=round(start - i * step if direction == "high" else start + i * step, 2),
             n=6 + i)
        for i in range(n)
    ]


def _board(n=5, direction="high", unit="count", label="Most villagers", nicks=None):
    return dict(label=label, unit=unit, direction=direction,
                leaders=_leaders(n, direction, nicks=nicks), top_games=[])


_ELOS = {1000: 900, 1001: 950, 1002: 1010, 1003: 1100, 1004: 1250}


def _q(board=None, seed=7, ask=None, **kw):
    return pb.build_question("villagers", board or _board(), _ELOS,
                             random.Random(seed), seq=12, ask=ask, **kw)


# ── the sample floor ─────────────────────────────────────────────────────
def test_a_board_with_too_few_leaders_yields_no_question():
    """Four options is the card's arity, not a preference (nammaoe2bot/features/quiz/view.py
    renders A-D). Three leaders cannot make a fair four-option question and
    must produce nothing at all rather than a padded or shrunken one."""
    assert _q(_board(n=3)) is None
    assert _q(_board(n=2)) is None
    assert _q(_board(n=0)) is None


def test_exactly_the_minimum_number_of_leaders_still_produces_a_question():
    q = _q(_board(n=pb.MIN_LEADERS))
    assert q is not None
    assert len(q["options"]) == pb.MIN_OPTIONS


def test_generate_returns_nothing_when_every_board_is_below_the_floor():
    boards = {"villagers": _board(n=3), "eapm": _board(n=1), "military": _board(n=0)}
    assert pb.generate(boards, _ELOS, seq=1, rng=random.Random(3)) is None


def test_hidden_players_are_dropped_before_the_floor_is_applied():
    """A board with 5 leaders, 2 of them opted out of public listing, has 3
    askable leaders — below the floor, so no question."""
    assert _q(_board(n=5), exclude_user_ids={1000, 1004}) is None
    q = _q(_board(n=6), exclude_user_ids={1000})
    assert 1000 not in q["meta"]["option_user_ids"]


# ── what goes in the options vs. the reveal ──────────────────────────────
def test_options_carry_name_and_elo_and_nothing_else():
    q = _q()
    for option, user_id in zip(q["options"], q["meta"]["option_user_ids"]):
        nick = f"p{user_id - 1000}"
        assert option == f"{nick} (Elo {_ELOS[user_id]})"


def test_no_metric_value_ever_reaches_an_option():
    """The value IS the answer. An option carrying it turns the quiz into a
    reading exercise — the offline bank's rule, preserved."""
    board = _board()
    values = {str(leader["avg"]) for leader in board["leaders"]}
    q = _q(board)
    for option in q["options"]:
        assert not any(v in option for v in values)


def test_a_player_with_no_elo_renders_as_a_bare_name():
    q = pb.build_question("villagers", _board(), {1000: 900}, random.Random(7), seq=12)
    labelled = [o for o in q["options"] if "(Elo" in o]
    assert len(labelled) == 1
    assert "p0 (Elo 900)" in q["options"]


def test_the_reveal_carries_every_value_with_the_sample_it_rests_on():
    board = _board()
    q = _q(board)
    shown = {leader["user_id"]: leader for leader in board["leaders"]}
    for user_id in q["meta"]["option_user_ids"]:
        leader = shown[user_id]
        assert str(leader["avg"]) in q["explanation"]
        assert f"({leader['n']} games)" in q["explanation"]


def test_a_seconds_metric_reveals_a_time_not_a_raw_count():
    board = dict(label="Fastest to Feudal Age", unit="seconds", direction="low",
                 leaders=[dict(user_id=1000 + i, nick=f"p{i}", avg=600 + 65 * i, n=9)
                          for i in range(4)],
                 top_games=[])
    q = pb.build_question("feudal_s", board, _ELOS, random.Random(4), seq=3)
    assert "10:00" in q["explanation"]
    assert "11:05" in q["explanation"]


# ── identity binds on user_id, never on the rendered name ────────────────
def test_the_answer_is_found_by_user_id_when_two_players_share_a_nick():
    """Two members can render the same display name, and `nick` here is an
    in-game name observed in a replay. Matching the answer by name would mark
    whichever twin the shuffle happened to place first."""
    board = _board(n=4, nicks=["twin", "b", "twin", "d"])
    decoy_first = 0
    for seed in range(12):
        q = pb.build_question("villagers", board, _ELOS, random.Random(seed), seq=1, ask="best")
        assert q is not None
        order = q["meta"]["option_user_ids"]
        assert order[q["correct_index"]] == 1000            # the leader, by id
        assert q["correct_indices"] == [q["correct_index"]]
        if order.index(1002) < order.index(1000):
            decoy_first += 1
    # Proves this test can actually see the bug: on some seeds the same-named
    # decoy is rendered BEFORE the answer, so a nick match would pick it.
    assert decoy_first > 0


def test_the_option_order_records_the_user_id_behind_every_slot():
    q = _q()
    assert len(q["meta"]["option_user_ids"]) == len(q["options"])
    assert q["meta"]["answer_user_id"] == q["meta"]["option_user_ids"][q["correct_index"]]


# ── direction, ties, and the shape of the record ─────────────────────────
def test_best_asks_for_the_head_of_the_window_and_worst_for_its_tail():
    board = _board(n=pb.MIN_LEADERS)                     # window is the whole board
    best = _q(board, ask="best")
    worst = _q(board, ask="worst")
    assert best["meta"]["answer_user_id"] == 1000        # leaders[0] wins the metric
    assert worst["meta"]["answer_user_id"] == 1003


def test_direction_is_taken_from_the_boards_own_ordering_not_re_derived():
    """A 'low' metric's leaders ascend. compute_board already applied that;
    this module must not sort again (nammaoe2bot/derived/boards.py keeps direction in
    one place)."""
    low = _board(n=pb.MIN_LEADERS, direction="low", unit="seconds",
                 label="Fastest to Castle Age")
    assert low["leaders"][0]["avg"] < low["leaders"][-1]["avg"]
    assert _q(low, ask="best")["meta"]["answer_user_id"] == 1000


def test_a_tie_for_the_place_being_asked_about_is_dropped():
    board = _board(n=pb.MIN_LEADERS)
    board["leaders"][0]["avg"] = board["leaders"][1]["avg"]
    assert _q(board, ask="best") is None
    assert _q(board, ask="worst") is not None            # the tail is still unambiguous


def test_the_record_matches_what_the_store_and_the_card_read():
    q = _q()
    for key in ("id", "category", "difficulty", "prompt", "options",
                "correct_indices", "correct_index", "explanation", "source"):
        assert key in q, key
    assert q["source"] == "player"
    assert q["multi"] is False
    assert q["category"] != "techgaps"     # techgaps is the multi-answer category
    assert q["id"] == "player:villagers:12"
    assert pb.metric_of_id(q["id"]) == "villagers"


def test_metric_categories_cover_the_whole_catalog():
    """A metric added to nammaoe2bot/derived/boards.METRICS with no category here would
    ship questions labelled with the fallback and nobody would notice."""
    assert set(derived_boards.METRICS) <= set(pb.CATEGORIES)


def test_metric_of_id_ignores_a_game_question_id():
    assert pb.metric_of_id("combat_00010") is None
    assert pb.metrics_of_ids(["combat_00010", "player:eapm:4", None]) == ["eapm"]


def test_the_same_seed_produces_the_same_question():
    boards = {"villagers": _board(), "eapm": _board(label="Highest average eAPM", unit="eapm")}
    a = pb.generate(boards, _ELOS, seq=5, rng=random.Random("c:5"))
    b = pb.generate(boards, _ELOS, seq=5, rng=random.Random("c:5"))
    assert a == b


def test_a_recently_used_metric_is_avoided_but_never_silences_the_day():
    boards = {"villagers": _board(), "eapm": _board(label="Highest average eAPM", unit="eapm")}
    q = pb.generate(boards, _ELOS, seq=5, rng=random.Random(1), avoid_metrics={"villagers"})
    assert q["meta"]["metric_id"] == "eapm"
    # Every board recently used -> still a question, rather than a silent day.
    q = pb.generate(boards, _ELOS, seq=5, rng=random.Random(1),
                    avoid_metrics={"villagers", "eapm"})
    assert q is not None


# ── the async shell ──────────────────────────────────────────────────────
class _FakeDb:
    def __init__(self, boards_rows, rating_rows):
        self.rows = {pb._BOARDS_SQL: boards_rows, pb._RATINGS_SQL: rating_rows}

    async def fetchall(self, sql, args=None):
        return self.rows[sql]


def test_fetch_inputs_parses_boards_and_reduces_ratings(monkeypatch):
    fake = _FakeDb(
        [{"metric_id": "villagers", "board": '{"label": "L", "unit": "count", '
                                             '"direction": "high", "leaders": [], "top_games": []}'},
         {"metric_id": "broken", "board": "{not json"}],
        [{"user_id": 1, "rating": 1200, "is_hidden": 0, "last_at": 100},
         {"user_id": 1, "rating": 1310, "is_hidden": 0, "last_at": 500},
         {"user_id": 2, "rating": 990, "is_hidden": 1, "last_at": 400}])
    monkeypatch.setattr(pb, "db", fake)
    boards, elos, hidden = asyncio.run(pb.fetch_inputs(7))
    assert set(boards) == {"villagers"}            # the corrupt blob is skipped, not raised on
    assert elos == {1: 1310, 2: 990}               # newest ranked row per user wins
    assert hidden == {2}


def test_pick_elos_is_independent_of_row_order():
    rows = [{"user_id": 1, "rating": 1200, "is_hidden": 0, "last_at": 500},
            {"user_id": 1, "rating": 900, "is_hidden": 0, "last_at": 100},
            {"user_id": 3, "rating": None, "is_hidden": 0, "last_at": 900}]
    assert pb.pick_elos(rows)[0] == pb.pick_elos(list(reversed(rows)))[0] == {1: 1200}


def test_question_for_channel_is_none_for_an_unenrolled_channel(monkeypatch):
    import nammaoe2bot.community as community
    monkeypatch.setattr(community, "community_for_channel",
                        lambda _cid: asyncio.sleep(0, result=None))
    assert asyncio.run(pb.question_for_channel(123, 1)) is None


# ── day parity: which bank fills a slot ──────────────────────────────────
# `from nammaoe2bot.features.quiz import jobs` hands back the QuizJobs SINGLETON, not the module:
# nammaoe2bot/features/quiz/__init__.py rebinds the name with `from .jobs import jobs`.
import nammaoe2bot.features.quiz.jobs                                       # noqa: E402
import sys                                                 # noqa: E402

_JOBS = sys.modules["nammaoe2bot.features.quiz.jobs"]

_LIVE = dict(id="player:eapm:1", source="player", category="eAPM", prompt="p",
             options=["a", "b", "c", "d"], correct_indices=[0], explanation="e",
             difficulty="hard")


def _next(monkeypatch, seq, day, live=None, asked=()):
    """Drive QuizJobs._next_question with fakes for both banks."""
    async def _fake_live(_channel_id, _seq, _avoid=()):
        return live

    async def _fake_recent(_channel_id, _n=6):
        return []

    async def _fake_asked(_channel_id):
        return set(asked)

    monkeypatch.setattr(_JOBS.player_bank, "question_for_channel", _fake_live)
    monkeypatch.setattr(_JOBS.store, "recent_question_ids", _fake_recent)
    monkeypatch.setattr(_JOBS.store, "asked_ids", _fake_asked)
    return asyncio.run(_JOBS.QuizJobs()._next_question(1, seq, day))


def test_player_days_take_the_live_bank_and_game_days_the_committed_schedule(monkeypatch):
    for seq in range(1, 15):
        week, day = sched.slot_for_seq(seq)
        got = _next(monkeypatch, seq, day, live=_LIVE)
        if day % 2 == 1:
            assert got["source"] == "player", (week, day)
        else:
            assert got["source"] == "game", (week, day)
            assert got in _JOBS._SCHEDULE          # the committed file, unchanged


def test_a_game_day_walks_the_committed_queue_past_what_was_already_asked(monkeypatch):
    first, second = _JOBS._SCHEDULE[0], _JOBS._SCHEDULE[1]
    assert _next(monkeypatch, 2, day=2, live=_LIVE)["id"] == first["id"]
    assert _next(monkeypatch, 2, day=2, live=_LIVE,
                 asked={first["id"]})["id"] == second["id"]


def test_a_player_day_with_no_usable_board_falls_back_to_the_game_bank(monkeypatch):
    """Omit rather than degrade: a young community gets a real game question,
    never a padded player one."""
    got = _next(monkeypatch, 1, day=1, live=None)
    assert got["source"] == "game"
