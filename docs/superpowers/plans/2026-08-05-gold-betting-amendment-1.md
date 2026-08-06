# Gold Betting — Amendment 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let match participants bet on their own team only, surface self-bets in the post-match report, and let any bettor cancel their whole bet before the freeze.

**Architecture:** Both features hang off machinery that already exists. The own-team rule is a guard change in `bot/predictions/interactions.py` plus a persisted `prediction_bets.is_player` flag (the roster is in-memory and gone by report time). Cancel is a new `gold.cancel_bet()` whose exactly-once guarantee comes from the `DELETE`'s rowcount rather than an `idem_key`, driven by a button on the ephemeral confirmation and routed through the same global `on_interaction` chain as the bet buttons.

**Tech Stack:** Python 3.11, nextcord, aiomysql, MySQL 8, pytest.

## Global Constraints

- Design contract: `docs/superpowers/specs/2026-08-05-gold-betting-design.md`, **Amendment 1** section. Read it before starting.
- Players bet **own team only**; the opposing side is refused. Spectators: either side, unchanged.
- Self-bets go in the **same pool** — no separate pot, no adjusted odds.
- Cancel returns the **entire** stake, deletes the bet row, releases the side lock. No partial cancel.
- Cancel is allowed only while the post is `open` **and** `now < freezes_at`. Never after freeze.
- Cancel's exactly-once guard is the `DELETE`'s **rowcount**, NOT an `idem_key`. The `cancel` ledger row carries `idem_key = NULL`.
- **`cancel_bet` MUST re-read and row-lock the post (`SELECT … FOR UPDATE`) inside its own transaction, exactly as `place_bet` now does.** Read `place_bet`'s comment block in `bot/predictions/gold.py` before writing it — it explains the race in full. The mirror of that race applies to cancel and duplicates gold rather than destroying it: a sweep does `close_betting` → `bets_for` (snapshot) → `refund_post`; a cancel committing after the snapshot but before the status flip pays the user their stake back via its own `cancel` row AND again via the sweep's `refund:{post_id}:{user_id}` row. `reconcile()` cannot detect it, because the ledger and the balance cache agree perfectly. The `FOR UPDATE` on `prediction_posts` serialises the two: either the cancel commits first and the snapshot never sees the bet, or the flip to `frozen` commits first and the cancel sees it and refuses.
- `Match.teams` has THREE entries — `teams[0]`, `teams[1]`, and `teams[2]` = `"unpicked"` (idx=-1). Index the first two explicitly; never iterate `match.teams`.
- `bot/`, `core/`, `tests/` use **tabs**. Match the file you edit; never mix.
- After every task: `ruff check .` and `pytest tests/` must both pass.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 11: `is_player` — persist it, and enforce own-team-only

**Files:**
- Modify: `bot/predictions/__init__.py` (add the column to the `prediction_bets` declaration)
- Modify: `bot/predictions/flow.py` (add `_team_ids`, keep `_player_ids`)
- Modify: `bot/predictions/gold.py` (`place_bet` takes and stores `is_player`)
- Modify: `bot/predictions/store.py` (`bets_for` selects `is_player`)
- Modify: `bot/predictions/interactions.py` (the guard)
- Test: `tests/test_predictions_gold.py`, `tests/test_predictions_interactions.py`

**Interfaces:**
- Produces: `flow._team_ids(match_id) -> (set, set)` — per-side player ids for a live match, `(set(), set())` when the match is not in `bot.active_matches`.
- Produces: `gold.place_bet(community_id, user_id, post_id, side, stake, nick, now, is_player=False)` — the new final parameter is stored on the bets row (both the INSERT and the same-side UPDATE path).
- `store.bets_for(post_id)` rows gain `is_player`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_predictions_interactions.py`, add a class covering the guard (follow the existing file's `wire(...)` / `FakeInteraction` helpers — read them first; the fake match roster is already wired for the spectators-only tests, extend it to have two distinct sides):

```python
class TestOwnTeamOnly:
	def test_a_player_may_back_their_own_team(self, monkeypatch):
		""" The whole point of the amendment: a participant's gold can join the
		pool on the side they are actually playing. """
		bank = wire(monkeypatch, team0={7}, team1={8})
		i = FakeInteraction(user_id=7, custom_id="bet:12:0:50")
		run(i)
		assert bank.placed, "the bet never reached the bank"
		assert bank.placed[-1]["is_player"] is True

	def test_a_player_backing_the_other_team_is_refused(self, monkeypatch):
		""" A participant must never be able to hold a position against
		themselves — that is the only reason letting players bet is safe. """
		bank = wire(monkeypatch, team0={7}, team1={8})
		i = FakeInteraction(user_id=7, custom_id="bet:12:1:50")
		run(i)
		assert not bank.placed, "the bank was reached on a forbidden side"
		assert "only bet on yourself" in i.reply

	def test_a_spectator_may_back_either_side(self, monkeypatch):
		for side in (0, 1):
			bank = wire(monkeypatch, team0={7}, team1={8})
			i = FakeInteraction(user_id=99, custom_id=f"bet:12:{side}:50")
			run(i)
			assert bank.placed, f"spectator refused on side {side}"
			assert bank.placed[-1]["is_player"] is False

	def test_the_unpicked_pseudo_team_is_not_a_side(self, monkeypatch):
		""" Match.teams[2] is 'unpicked'. Someone sitting there is not playing
		either side and must be treated as a spectator, not silently blocked. """
		bank = wire(monkeypatch, team0={7}, team1={8}, unpicked={42})
		i = FakeInteraction(user_id=42, custom_id="bet:12:1:50")
		run(i)
		assert bank.placed
		assert bank.placed[-1]["is_player"] is False
```

In `tests/test_predictions_gold.py`, add to the `TestPlaceBet` class:

```python
	def test_is_player_is_written_on_the_bets_row(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 0, 1, 1]      # balance ok, same-side UPDATE misses -> INSERT
		asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000, is_player=True))
		row = next(c[2] for c in fake.calls if c[0] == "insert" and c[1] == "prediction_bets")
		assert row["is_player"] == 1

	def test_is_player_defaults_to_false_for_spectators(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 0, 1, 1]
		asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))
		row = next(c[2] for c in fake.calls if c[0] == "insert" and c[1] == "prediction_bets")
		assert row["is_player"] == 0
```

Note on the existing `TestPlaceBet` rowcount scripts: adding a parameter must not change the statement ORDER. If your `place_bet` edit changes how many statements run, the existing tests' `rowcounts` lists break — that is a signal you changed control flow, not just data. Keep the flow identical.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictions_interactions.py tests/test_predictions_gold.py -v -k "OwnTeam or is_player"`
Expected: FAIL — `wire()` has no `team0`/`team1` parameters and `place_bet` has no `is_player`.

- [ ] **Step 3: Implement**

`bot/predictions/__init__.py` — add to the `prediction_bets` columns list, after `side`:

```python
		# Captured at press time, NOT recomputed at report time: the roster
		# lives in bot.active_matches (in memory) and the match is gone from
		# there before the result is reported. Without this column the
		# post-match report cannot tell who backed themselves.
		dict(cname="is_player", ctype=db.types.bool, default=0),
```

`bot/predictions/flow.py` — add beside `_player_ids` (keep `_player_ids`; it is still the right shape for callers that only need "is this person playing"):

```python
def _team_ids(match_id):
	"""(team0_ids, team1_ids) for a live match, or two empty sets.

	Match.teams has THREE entries — [0], [1] and a "unpicked" pseudo-team at
	[2] with idx=-1 — so this indexes the two real sides explicitly. Iterating
	match.teams would turn unpicked players into a third side.
	"""
	import bot
	for m in bot.active_matches:
		if m.id == match_id:
			return {p.id for p in m.teams[0]}, {p.id for p in m.teams[1]}
	return set(), set()
```

`bot/predictions/gold.py` — `place_bet` gains the parameter and stores it. Change the signature to `(community_id, user_id, post_id, side, stake, nick, now, is_player=False)`, add `is_player=1 if is_player else 0` to the `tx.insert("prediction_bets", dict(...))` payload, and add `is_player=%s` to the same-side UPDATE's SET list (a player's later presses must not silently downgrade the flag, and re-asserting it is cheaper than reasoning about whether it can change). Update the docstring to name the new parameter.

`bot/predictions/store.py` — `bets_for`: add `is_player` to the SELECT list and to the docstring's row shape.

`bot/predictions/interactions.py` — replace the spectators-only guard:

```python
		# Participants may bet, but only on themselves: a player who could take
		# the opposing side could profit by losing. Spectators are unrestricted.
		team0, team1 = flow._team_ids(post["match_id"])
		is_player = interaction.user.id in team0 or interaction.user.id in team1
		if is_player and interaction.user.id not in (team0 if side == 0 else team1):
			return await _eph(interaction, "You can only bet on yourself — back your own team.")
```

and pass `is_player` through to `gold.place_bet(...)`.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_predictions_interactions.py tests/test_predictions_gold.py -v`
Expected: all PASS.

- [ ] **Step 5: Prove the guard is non-vacuous by MUTATION**

Invert the guard (drop the `is_player and` conjunct, or flip the `not in` to `in`), run `pytest tests/test_predictions_interactions.py -k OwnTeam`, and CONFIRM the forbidden-side test FAILS. Restore, confirm green, and confirm `git diff` is clean of the mutation. Record the actual output in your report.

- [ ] **Step 6: Full check + commit**

Run: `ruff check . && pytest tests/ -q`

```bash
git add -A
git commit -m "feat(betting): players may back their own team, and the bet row remembers that they did

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: the report names who backed themselves

**Files:**
- Modify: `bot/predictions/view.py` (`report_lines`)
- Test: `tests/test_predictions.py`

**Interfaces:**
- Consumes: `is_player` on the bet rows (Task 11).
- `report_lines(team0, team1, winner_idx, bets, paid, max_named=25)` — signature unchanged; behaviour gains the annotation. Rows without an `is_player` key must render exactly as before (use `.get`), so historical posts and any caller that has not been updated cannot crash.

- [ ] **Step 1: Write the failing tests**

Add to `TestReportLines` in `tests/test_predictions.py`:

```python
	SELF_BETS = [
		dict(user_id=1, nick="anu", side=0, stake=150, is_player=1),
		dict(user_id=2, nick="bala", side=0, stake=50, is_player=0),
		dict(user_id=3, nick="chetan", side=1, stake=100, is_player=1),
	]

	def test_a_player_who_backed_themselves_and_won_is_named_as_such(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.SELF_BETS, {1: 225, 2: 75}))
		anu = next(ln for ln in text.split("\n") if "anu" in ln)
		assert "backed themselves" in anu

	def test_a_player_who_backed_themselves_and_lost_is_named_as_such(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.SELF_BETS, {1: 225, 2: 75}))
		chetan = next(ln for ln in text.split("\n") if "chetan" in ln)
		assert "backed themselves" in chetan

	def test_a_spectator_is_not_annotated(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.SELF_BETS, {1: 225, 2: 75}))
		bala = next(ln for ln in text.split("\n") if "bala" in ln)
		assert "backed themselves" not in bala

	def test_rows_without_the_flag_still_render(self):
		# Historical posts predate the column; they must not crash the report.
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.BETS, {1: 225, 2: 75}))
		assert "anu" in text and "backed themselves" not in text
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictions.py -v -k "backed or without_the_flag"`
Expected: FAIL — no annotation is produced.

- [ ] **Step 3: Implement**

In `bot/predictions/view.py`, add above `report_lines`:

```python
def _backed_note(bet):
	"""Annotation for a participant who staked on their own team. `.get` because
	posts made before the is_player column exists carry no flag at all."""
	return " *(backed themselves)*" if bet.get("is_player") else ""
```

and thread it into both `_named` formatters inside `report_lines` — the winner line becomes

```python
			f"\U0001F3C6 **{b['nick']}**{_backed_note(b)} staked {b['stake']} → "
			f"**{paid.get(b['user_id'], 0)}** {GOLD} (+{paid.get(b['user_id'], 0) - b['stake']})"
```

and the loser line

```python
			f"\U0001F4B8 {b['nick']}{_backed_note(b)} staked {b['stake']} on "
			f"{team0 if b['side'] == 0 else team1} — gone"
```

Update `report_lines`' docstring to say the lists annotate participants who backed their own team.

- [ ] **Step 4: Run the tests, then full check + commit**

Run: `pytest tests/test_predictions.py -v && ruff check . && pytest tests/ -q`

```bash
git add -A
git commit -m "feat(betting): the report says who backed themselves, and whether it paid

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: `gold.cancel_bet` — the row is the refund token

**Files:**
- Modify: `bot/predictions/gold.py`
- Modify: `bot/predictions/view.py` (`_ENTRY_LABELS` gains `cancel`)
- Modify: `bot/predictions/__init__.py` (the `entry_type` comment on `gold_ledger`)
- Test: `tests/test_predictions_gold.py`

**Interfaces:**
- Produces: `gold.cancel_bet(community_id, user_id, post_id, now) -> (status, amount)` where status is `'ok'` (amount = gold returned), `'nothing'` (amount = 0, no bet row existed), or `'closed'` (amount = 0, the book is no longer open — checked under the row lock, authoritatively).

**READ FIRST:** `place_bet` in `bot/predictions/gold.py`, specifically its `FOR UPDATE` book re-read and the long comment above it. `cancel_bet` follows the identical discipline and the identical statement ordering. Also read the current fakes in `tests/test_predictions_gold.py` (`use_fake`, `fake.answer(...)`, `fake.rowcounts`) — they have evolved past what earlier tasks used, and the scripts below assume the current shape.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_predictions_gold.py`:

The fake's exact API has evolved (`use_fake`, `fake.answer(**cols)`, `fake.rowcounts`) — **read `tests/test_predictions_gold.py` and follow what is actually there.** The scripts below assume `answer()` supplies the columns the two `fetchone`s ask for, and that `rowcounts` is consumed left-to-right by `execute`/`insert` only. Adapt if the real fake differs; keep the assertions.

```python
class TestCancelBet:
	OPEN = dict(status="open", freezes_at=9999)

	def test_cancelling_returns_the_whole_stake_and_ledgers_it(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.answer(**self.OPEN, stake=60)
		fake.rowcounts = [1, 1, 1]      # DELETE hits, ledger insert, balance bump
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("ok", 60)
		row = next(c[2] for c in fake.calls if c[0] == "insert" and c[1] == "gold_ledger")
		assert row["entry_type"] == "cancel" and row["amount"] == 60

	def test_the_cancel_ledger_row_carries_no_idem_key(self, monkeypatch):
		""" A user may bet -> cancel -> bet -> cancel on one post, so a
		cancel:{post}:{user} key would swallow the second cancel and silently
		keep their gold. The DELETE's rowcount is the guard instead. """
		fake = use_fake(monkeypatch)
		fake.answer(**self.OPEN, stake=60)
		fake.rowcounts = [1, 1, 1]
		asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		row = next(c[2] for c in fake.calls if c[0] == "insert" and c[1] == "gold_ledger")
		assert row.get("idem_key") is None

	def test_a_second_cancel_refunds_nothing(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.answer(**self.OPEN, stake=None)     # the bets row is already gone
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("nothing", 0)
		assert not [c for c in fake.calls if c[0] == "insert"]

	def test_a_delete_that_matches_nothing_refunds_nothing(self, monkeypatch):
		""" Two presses both read the row; one DELETEs it first. The loser's
		DELETE affects 0 rows and must not pay out. """
		fake = use_fake(monkeypatch)
		fake.answer(**self.OPEN, stake=60)
		fake.rowcounts = [0]             # DELETE matched nothing
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("nothing", 0)
		assert not [c for c in fake.calls if c[0] == "insert"]

	def test_a_frozen_book_refuses_the_cancel_under_the_row_lock(self, monkeypatch):
		""" The mirror of place_bet's race. A cancel that slipped past a sweep's
		snapshot would be refunded twice — once here, once by the sweep's
		idempotent refund row — and reconcile() could never see it. """
		fake = use_fake(monkeypatch)
		fake.answer(status="frozen", freezes_at=9999)
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("closed", 0)
		assert not [c for c in fake.calls if c[0] == "insert"]

	def test_a_cancel_after_the_deadline_is_refused_even_while_open(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.answer(status="open", freezes_at=500)      # now=1000 is past it
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("closed", 0)
		assert not [c for c in fake.calls if c[0] == "insert"]

	def test_the_book_is_locked_before_the_bet_row_is_read(self, monkeypatch):
		""" Ordering is the guarantee: lock the post, THEN touch the money. """
		fake = use_fake(monkeypatch)
		fake.answer(status="open", freezes_at=9999, stake=60)
		fake.rowcounts = [1, 1, 1]
		asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		reads = [c[1] for c in fake.calls if c[0] == "fetchone"]
		assert "prediction_posts" in reads[0] and "FOR UPDATE" in reads[0]

	def test_a_missing_balance_row_rolls_the_refund_back(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"stake": 60}
		fake.rowcounts = [1, 1, 0]       # DELETE ok, ledger ok, balance UPDATE misses
		try:
			asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
			assert False, "should have raised"
		except RuntimeError:
			pass
		assert fake.rolled_back
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictions_gold.py -v -k Cancel`
Expected: FAIL — `gold` has no attribute `cancel_bet`.

- [ ] **Step 3: Implement in `bot/predictions/gold.py`**

```python
async def cancel_bet(community_id, user_id, post_id, now):
	"""Back out of a bet entirely, before the freeze. -> ('ok', refunded) | ('nothing', 0).

	EXACTLY-ONCE WITHOUT AN IDEM_KEY, and that is deliberate. Every other credit
	here is made once by a UNIQUE idem_key, but a user may bet -> cancel -> bet
	-> cancel on ONE post, so cancel:{post_id}:{user_id} is not unique and the
	second cancel would be swallowed as "already applied". The prediction_bets
	ROW is the refund token instead: the DELETE's rowcount is the guard, so a
	double press finds no row and refunds nothing. Same discipline as
	place_bet's conditional balance decrement.

	Deleting the row also releases the side lock — after cancelling, the user
	may bet again on either side (subject to the own-team rule if playing).

	THE BOOK IS RE-READ AND LOCKED HERE, not merely checked by the caller —
	see place_bet's comment for the race. Its mirror duplicates gold rather
	than destroying it: a sweep snapshots the book (store.bets_for) and then
	refunds what it found, so a cancel committing after that snapshot but
	before the status flip pays the stake back TWICE — once as this 'cancel'
	row, once as the sweep's idempotent refund:{post_id}:{user_id} row. The
	ledger and the balance cache would agree perfectly, so reconcile() would
	never see it. FOR UPDATE on prediction_posts serialises the two.
	"""
	async with db.transaction() as tx:
		book = await tx.fetchone(
			"SELECT status, freezes_at FROM prediction_posts WHERE id=%s FOR UPDATE",
			[post_id])
		if book is None or book["status"] != "open" or now >= int(book["freezes_at"]):
			return "closed", 0
		row = await tx.fetchone(
			"SELECT stake FROM prediction_bets WHERE post_id=%s AND user_id=%s FOR UPDATE",
			[post_id, user_id])
		if not row:
			return "nothing", 0
		stake = int(row["stake"])
		removed = await tx.execute(
			"DELETE FROM prediction_bets WHERE post_id=%s AND user_id=%s", [post_id, user_id])
		if not removed:
			return "nothing", 0
		await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id, entry_type="cancel",
			amount=stake, post_id=post_id, created_at=now))
		bumped = await tx.execute(
			"UPDATE gold_balances SET balance=balance+%s, updated_at=%s "
			"WHERE community_id=%s AND user_id=%s",
			[stake, now, community_id, user_id])
		if not bumped:
			raise RuntimeError(
				f"gold_balances row missing for {community_id}/{user_id} (cancel post {post_id})")
		return "ok", stake
```

In `bot/predictions/view.py`, add to `_ENTRY_LABELS`: `"cancel": "Bet cancelled",`.

In `bot/predictions/__init__.py`, extend the `entry_type` comment on the `gold_ledger` declaration to list `cancel`.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_predictions_gold.py -v`
Expected: all PASS.

- [ ] **Step 5: Prove BOTH guards by MUTATION**

Two separate mutations, each applied alone, run, observed, restored:
1. Delete the `FOR UPDATE` book check (the four lines from `book = await tx.fetchone` through `return "closed", 0`). `pytest tests/test_predictions_gold.py -k Cancel` MUST fail on the frozen-book and past-deadline tests. This is the gold-duplication guard.
2. Change `if not removed:` to `if False:`. The delete-matched-nothing test MUST fail. This is the double-refund guard.

Confirm `git status --porcelain` is clean after restoring. Record the real failure output for both in your report. A mutation that does not fail means the test is decorative — fix the test, not the report.

- [ ] **Step 6: Full check + commit**

Run: `ruff check . && pytest tests/ -q`

```bash
git add -A
git commit -m "feat(betting): cancel a bet before the freeze — the bets row is the refund token

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: the Cancel button, and its route

**Files:**
- Modify: `bot/predictions/scoring.py` (`parse_cancel_custom_id`)
- Modify: `bot/predictions/view.py` (`bet_cancelled_lines`, `nothing_to_cancel_notice`)
- Modify: `bot/predictions/embeds.py` (`cancel_view`)
- Modify: `bot/predictions/interactions.py` (attach the view; route the press)
- Test: `tests/test_predictions.py`, `tests/test_predictions_interactions.py`

**Interfaces:**
- Produces: `scoring.parse_cancel_custom_id(cid) -> post_id | None` for `'betcancel:{post_id}'`.
- Produces: `embeds.cancel_view(post_id)` — a one-button `ui.View(timeout=None, auto_defer=False)`, `ButtonStyle.secondary`, label "Cancel my bet", `custom_id=f"betcancel:{post_id}"`.
- The ephemeral confirmation sent after a successful press now carries that view.

- [ ] **Step 1: Write the failing tests**

In `tests/test_predictions.py`, beside the other scoring-router tests:

```python
class TestParseCancelCustomId:
	def test_parses_a_cancel(self):
		assert scoring.parse_cancel_custom_id("betcancel:12") == 12

	def test_a_bet_custom_id_is_not_a_cancel(self):
		# 'bet:' is a prefix of 'betcancel:' only in the other direction, but be sure.
		assert scoring.parse_cancel_custom_id("bet:12:0:50") is None

	def test_a_cancel_custom_id_is_not_a_bet(self):
		assert scoring.parse_bet_custom_id("betcancel:12") is None

	def test_garbage_is_none(self):
		assert scoring.parse_cancel_custom_id("betcancel:x") is None
		assert scoring.parse_cancel_custom_id("") is None
```

In `tests/test_predictions_interactions.py`:

```python
class TestCancelRoute:
	def test_cancelling_refunds_and_rewrites_the_private_message(self, monkeypatch):
		bank = wire(monkeypatch, cancel_bet=("ok", 60))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert bank.cancelled, "the bank was never asked to cancel"
		assert "60" in i.reply and "cancel" in i.reply.lower()

	def test_cancelling_after_the_freeze_is_refused(self, monkeypatch):
		bank = wire(monkeypatch, cancel_bet=("ok", 60), post_frozen=True)
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert not bank.cancelled, "gold was returned after the book locked"

	def test_cancelling_with_no_bet_says_so_and_moves_no_gold(self, monkeypatch):
		bank = wire(monkeypatch, cancel_bet=("nothing", 0))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert "no bet" in i.reply.lower()

	def test_a_foreign_custom_id_is_ignored(self, monkeypatch):
		bank = wire(monkeypatch)
		i = FakeInteraction(user_id=99, custom_id="quiz:12:reveal")
		run(i)
		assert not bank.cancelled and not bank.placed
```

Extend the file's `wire(...)` helper with `cancel_bet=` and `post_frozen=` knobs in the same style as the existing ones.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictions.py tests/test_predictions_interactions.py -v -k "Cancel"`
Expected: FAIL — `parse_cancel_custom_id` does not exist.

- [ ] **Step 3: Implement**

`bot/predictions/scoring.py`:

```python
def parse_cancel_custom_id(cid):
	"""'betcancel:{post_id}' -> post_id; anything else -> None."""
	if not cid or not cid.startswith("betcancel:"):
		return None
	parts = cid.split(":")
	if len(parts) != 2:
		return None
	try:
		return int(parts[1])
	except ValueError:
		return None
```

**Check `parse_bet_custom_id` rejects `betcancel:…`.** It requires exactly 4 colon-separated parts and a `"bet:"` prefix; `"betcancel:12"` has 2 parts, so it returns None. Confirm with the test above rather than assuming.

`bot/predictions/view.py`:

```python
def bet_cancelled_lines(amount, balance_after):
	return [f"Bet cancelled — **{amount}** {GOLD} returned.",
			f"Your balance: **{balance_after}** {GOLD}. You can bet again while the book is open."]


NOTHING_TO_CANCEL_NOTICE = "You have no bet on this match to cancel."
```

`bot/predictions/embeds.py`:

```python
def cancel_view(post_id):
	# Same rules as bet_view: routed by the global on_interaction handler, so
	# timeout=None and auto_defer=False.
	v = ui.View(timeout=None, auto_defer=False)
	v.add_item(ui.Button(
		style=ButtonStyle.secondary, label="Cancel my bet",
		custom_id=f"betcancel:{post_id}"))
	return v
```

`bot/predictions/interactions.py`:
1. Attach `view=embeds.cancel_view(post_id)` to the successful-press ephemeral. `_eph` currently takes only text — extend it to accept an optional `view=None` and pass it through to both the `send_message` and `followup.send` branches.
2. Route the cancel press. Put the check **before** the bet route, and give it its own guard chain:

```python
		cancel_post_id = parse_cancel_custom_id((interaction.data or {}).get("custom_id", ""))
		if cancel_post_id is not None:
			return await _handle_cancel(interaction, cancel_post_id, int(time.time()))
```

with:

```python
async def _handle_cancel(interaction, post_id, now):
	"""Back out before the freeze.

	The check below is a fast path for a friendly message on a stale button —
	it is NOT the guard. gold.cancel_bet re-reads and row-locks the post inside
	its own transaction and is the only authority on whether the book is still
	open; a status read out here is stale the moment it returns.
	"""
	post = await store.get_post(post_id)
	if not post:
		return await _eph(interaction, "Betting on this match is closed — bets can no longer be cancelled.")
	from bot import community
	community_id = await community.community_for_channel(post["channel_id"])
	if community_id is None:
		return await _eph(interaction, "This channel keeps no stats — there is no gold here.")
	status, amount = await gold.cancel_bet(community_id, interaction.user.id, post_id, now)
	if status == "closed":
		return await _eph(interaction, "Betting on this match is closed — bets can no longer be cancelled.")
	if status == "nothing":
		return await _eph(interaction, view.NOTHING_TO_CANCEL_NOTICE)
	balance = await gold.balance(community_id, interaction.user.id)
	await _eph(interaction, "\n".join(view.bet_cancelled_lines(amount, balance)))
	bets = await store.bets_for(post_id)
	pool0, pool1 = pools(bets)
	await _refresh_card(post, pool0, pool1, now)
```

The public card must be refreshed — cancelling changes the pools and therefore the odds everyone else is looking at.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_predictions.py tests/test_predictions_interactions.py -v`
Expected: all PASS.

- [ ] **Step 5: Prove the deadline guard by MUTATION**

Remove the `now >= post["freezes_at"]` clause, run `pytest tests/test_predictions_interactions.py -k "after_the_freeze"`, and CONFIRM IT FAILS. Restore, confirm green, confirm `git diff` is clean. Record the real output in your report.

- [ ] **Step 6: Full check + commit**

Run: `ruff check . && pytest tests/ -q`

```bash
git add -A
git commit -m "feat(betting): a Cancel button on the private confirmation, refunding the whole stake

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 15: docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-08-05-gold-betting-design.md` only if implementation deviated

- [ ] **Step 1: Update CLAUDE.md**

The betting paragraph added in the original Task 10 states "spectators-only" as an invariant. That is now **wrong**. Correct it and add the amendment's rules, in CLAUDE.md's existing dense factual voice:
- participants may bet **on their own team only**; the opposing side is refused, which is what keeps "no participant can profit by losing" true;
- `prediction_bets.is_player` is captured **at press time** because the roster lives in `bot.active_matches` and is gone by report time — it cannot be recomputed;
- `Match.teams` has three entries (the third is `"unpicked"`), so the own-team check indexes `[0]`/`[1]` explicitly;
- cancel returns the whole stake, only while the post is open and before `freezes_at`, and its exactly-once guard is the `DELETE`'s rowcount, **not** an `idem_key` — because bet→cancel→bet→cancel makes a per-post key non-unique.

- [ ] **Step 2: Verify and commit**

Run: `ruff check . && pytest tests/ -q`. Also `grep -n "spectator" CLAUDE.md` and confirm no stale spectators-only claim survives.

```bash
git add -A
git commit -m "docs(betting): amendment 1 — own-team bets and cancellation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
