# Check-in back-out + flexible /subauto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a check-in back-out remove only that player (swap in a queued player or revert to gathering) instead of aborting the whole match, extend `/subauto` to replace any named player in any match stage, and add a live check-in countdown plus a one-time 1-minute warning.

**Architecture:** The `CheckIn` stage owns a `replace_player` swap primitive (reusing `pick_available()` and `PickupQueue.revert()`); the `⛔` reaction, `/notready`, and `/subauto`-during-check-in all route through it. `/subauto` routes by match state — check-in uses `CheckIn.replace_player`, draft/waiting-report uses the existing rebalancing `Draft.sub_auto` (generalized to target any player). A new pure `should_warn` helper drives the 1-minute warning; Discord's `<t:UNIX:R>` markdown renders the live timer.

**Tech Stack:** Python 3.11, nextcord, pytest (pure-function tests), ruff (tabs, line-length 120).

---

## File map

- `bot/match/subbing.py` — add pure `should_warn()` helper (alongside existing `pick_available()`).
- `tests/test_subauto.py` — add `should_warn` tests next to the existing `pick_available` tests.
- `bot/match/check_in.py` — `replace_player`, `back_out`, `revert_single`, `end_time` property, warning in `think()`, `warned` flag; remove `discard_immediately`, `discarded_players`, `discard_member`, `abort_member`; simplify `refresh()` and `start()`.
- `bot/match/embeds.py` — check-in embed: add live timer field, remove the ❌ discarded marker, reword back-out text.
- `bot/match/draft.py` — generalize `sub_auto(ctx, author)` → `sub_auto(ctx, out_member)`.
- `bot/commands/matches.py` — `sub_auto` command: drop `@author_match`, add optional `player`, route by state.
- `bot/context/slash/commands.py` — `/subauto` optional `player` arg + description.
- `bot/queues/pickup_queue.py` — remove `check_in_discard_immediately` config var + pass-through.
- `bot/match/match.py` — remove `check_in_discard_immediately` from `default_cfg`.

`start.py`, `config.example.cfg`, and the web dashboard contain **no** references to `check_in_discard_immediately` (verified by grep), so they need no changes.

---

## Task 1: Add the pure `should_warn` helper (TDD)

**Files:**
- Modify: `bot/match/subbing.py`
- Test: `tests/test_subauto.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subauto.py`:

```python
from bot.match.subbing import should_warn


class TestShouldWarn:
	# end_time = 1000; the 1-minute window is [940, 1000].
	def test_fires_inside_final_minute_when_players_not_ready(self):
		assert should_warn(frame_time=950, end_time=1000, already_warned=False, num_not_ready=2) is True

	def test_fires_at_exact_window_start(self):
		assert should_warn(940, 1000, False, 1) is True

	def test_silent_before_final_minute(self):
		assert should_warn(900, 1000, False, 2) is False

	def test_silent_when_already_warned(self):
		assert should_warn(950, 1000, True, 2) is False

	def test_silent_when_everyone_ready(self):
		assert should_warn(950, 1000, False, 0) is False

	def test_silent_after_deadline_timeout_takes_over(self):
		assert should_warn(1001, 1000, False, 2) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subauto.py::TestShouldWarn -v`
Expected: FAIL with `ImportError: cannot import name 'should_warn'`.

- [ ] **Step 3: Write minimal implementation**

Append to `bot/match/subbing.py`:

```python
def should_warn(frame_time, end_time, already_warned, num_not_ready):
	"""True when the one-time 1-minute check-in warning should fire now.

	Fires once, only while players are still not ready, and only inside the
	final 60 seconds before ``end_time`` (not after the deadline itself —
	timeout handling takes over then).
	"""
	if already_warned or num_not_ready <= 0:
		return False
	return end_time - 60 <= frame_time <= end_time
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subauto.py -v`
Expected: PASS (all `TestPickAvailable` and `TestShouldWarn` tests).

- [ ] **Step 5: Commit**

```bash
git add bot/match/subbing.py tests/test_subauto.py
git commit -m "feat(check-in): add pure should_warn helper for 1-minute warning"
```

---

## Task 2: Remove the `check_in_discard_immediately` config variable

This deletes the old abort-whole-match knob everywhere it is declared/read, so the
rewrite in Task 3 doesn't reference a config that still triggers old behavior.

**Files:**
- Modify: `bot/queues/pickup_queue.py:82-89` (var def) and `:345` (pass-through)
- Modify: `bot/match/match.py:33` (default_cfg)

- [ ] **Step 1: Remove the config-variable definition**

In `bot/queues/pickup_queue.py`, delete the entire `BoolVar` block for
`check_in_discard_immediately` (the 8 lines starting at `Variables.BoolVar(` /
`"check_in_discard_immediately",` through its closing `),`):

```python
			Variables.BoolVar(
				"check_in_discard_immediately",
				display="Check-in discard immediately",
				section="General",
				default=1,
				description="Revert check-in state immediately when someone discards check-in",
				notnull=True
			),
```

- [ ] **Step 2: Remove the pass-through in `_match_cfg()`**

In `bot/queues/pickup_queue.py` around line 345, change:

```python
			check_in_discard=self.cfg.check_in_discard, check_in_discard_immediately=self.cfg.check_in_discard_immediately,
```

to:

```python
			check_in_discard=self.cfg.check_in_discard,
```

- [ ] **Step 3: Remove it from `default_cfg`**

In `bot/match/match.py:33`, change:

```python
		check_in_discard=True, check_in_discard_immediately=True, match_lifetime=3*60*60, start_msg=None, server=None,
```

to:

```python
		check_in_discard=True, match_lifetime=3*60*60, start_msg=None, server=None,
```

- [ ] **Step 4: Verify no references remain and code imports**

Run: `grep -rn "check_in_discard_immediately\|discard_immediately" bot/`
Expected: only `bot/match/check_in.py` still matches (lines 21, 136, 150 — removed in Task 3).

Run: `python -c "import ast; ast.parse(open('bot/queues/pickup_queue.py').read()); ast.parse(open('bot/match/match.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add bot/queues/pickup_queue.py bot/match/match.py
git commit -m "refactor(check-in): drop check_in_discard_immediately config var"
```

---

## Task 3: Rewrite `check_in.py` — swap/revert back-out, timer, warning

Replaces the immediate-abort discard model with single-player swap-or-revert,
adds the `end_time` property and 1-minute warning, and removes the now-dead
`discarded_players` machinery. The whole file is replaced for clarity.

**Files:**
- Modify: `bot/match/check_in.py` (full replacement)

- [ ] **Step 1: Replace the file contents**

Write `bot/match/check_in.py`:

```python
# -*- coding: utf-8 -*-
import mmap  # noqa: F401
import random
import bot
from nextcord.errors import DiscordException

from core.utils import join_and
from core.console import log  # noqa: F401
from bot.match.subbing import pick_available, should_warn


class CheckIn:

	READY_EMOJI = "☑"
	NOT_READY_EMOJI = "⛔"
	INT_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6⃣", "7⃣", "8⃣", "9⃣"]

	def __init__(self, match, timeout):
		self.m = match
		self.timeout = timeout
		self.allow_discard = self.m.cfg['check_in_discard']
		self.ready_players = set()
		self.warned = False
		self.message = None

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			self.ready_players.add(p)

		if len(self.m.cfg['maps']) > 1 and self.m.cfg['vote_maps']:
			self.maps = self.m.random_maps(self.m.cfg['maps'], self.m.cfg['vote_maps'], self.m.queue.last_maps)
			self.map_votes = [set() for i in self.maps]
		else:
			self.maps = []
			self.map_votes = []

		if self.timeout:
			self.m.states.append(self.m.CHECK_IN)

	@property
	def end_time(self):
		return int(self.m.start_time + self.timeout)

	async def think(self, frame_time):
		not_ready = [m for m in self.m.players if m not in self.ready_players]
		if should_warn(frame_time, self.end_time, self.warned, len(not_ready)):
			self.warned = True
			ctx = bot.SystemContext(self.m.qc)
			await ctx.notice(self.m.gt(
				"⏳ 1 minute left to check in, {members}! If you don't ready up you'll be removed "
				"and replaced by the next queued player, or the queue reverts to gathering."
			).format(members=join_and([m.mention for m in not_ready])))

		if frame_time > self.end_time:
			ctx = bot.SystemContext(self.m.qc)
			if self.allow_discard:
				await self.abort_timeout(ctx)
			else:
				await self.finish(ctx)

	async def start(self, ctx):
		text = f"!spawn message {self.m.id}"
		self.message = await ctx.channel.send(text)

		emojis = [self.READY_EMOJI, self.NOT_READY_EMOJI] if self.allow_discard else [self.READY_EMOJI]
		emojis += [self.INT_EMOJIS[n] for n in range(len(self.maps))]
		try:
			for emoji in emojis:
				await self.message.add_reaction(emoji)
		except DiscordException:
			pass
		bot.waiting_reactions[self.message.id] = self.process_reaction
		await self.refresh(ctx)

	async def refresh(self, ctx):
		not_ready = [m for m in self.m.players if m not in self.ready_players]
		if len(not_ready):
			try:
				await self.message.edit(content=None, embed=self.m.embeds.check_in(not_ready))
			except DiscordException:
				pass
		else:
			await self.finish(ctx)

	async def finish(self, ctx):
		bot.waiting_reactions.pop(self.message.id)
		self.ready_players = set()
		if len(self.maps):
			order = list(range(len(self.maps)))
			random.shuffle(order)
			order.sort(key=lambda n: len(self.map_votes[n]), reverse=True)
			self.m.maps = [self.maps[n] for n in order[:self.m.cfg['map_count']]]
		await self.message.delete()

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			bot.auto_ready.pop(p.id)

		await self.m.next_state(ctx)

	async def process_reaction(self, reaction, user, remove=False):
		if self.m.state != self.m.CHECK_IN or user not in self.m.players:
			return

		if str(reaction) in self.INT_EMOJIS:
			idx = self.INT_EMOJIS.index(str(reaction))
			if idx <= len(self.maps):
				if remove:
					self.map_votes[idx].discard(user.id)
					self.ready_players.discard(user)
				else:
					self.map_votes[idx].add(user.id)
					self.ready_players.add(user)
				await self.refresh(bot.SystemContext(self.m.queue.qc))

		elif str(reaction) == self.READY_EMOJI:
			if remove:
				self.ready_players.discard(user)
			else:
				self.ready_players.add(user)
			await self.refresh(bot.SystemContext(self.m.queue.qc))

		elif str(reaction) == self.NOT_READY_EMOJI and self.allow_discard:
			return await self.back_out(bot.SystemContext(self.m.queue.qc), user)

	async def set_ready(self, ctx, member, ready):
		if self.m.state != self.m.CHECK_IN:
			raise bot.Exc.MatchStateError(self.m.gt("The match is not on the check-in stage."))
		if ready:
			self.ready_players.add(member)
			await self.refresh(ctx)
		else:
			if not self.allow_discard:
				raise bot.Exc.PermissionError(self.m.gt("Discarding check-in is not allowed."))
			return await self.back_out(ctx, member)

	async def replace_player(self, ctx, out_member):
		"""Swap out_member for the next available queued player.

		Returns the swapped-in member, or None when no queued player is
		available (the caller decides what to do with None). The new player
		must check in themselves unless they have an active /auto_ready.
		"""
		busy_ids = {p.id for m in bot.active_matches for p in m.players}
		candidate = pick_available(self.m.queue.queue, busy_ids)
		if candidate is None:
			return None

		self.m.players.remove(out_member)
		self.m.players.append(candidate)
		self.ready_players.discard(out_member)
		if candidate.id in bot.auto_ready.keys():
			self.ready_players.add(candidate)

		await self.m.qc.remove_members(candidate, ctx=ctx)
		await bot.remove_players(candidate, reason="pickup started")
		return candidate

	async def back_out(self, ctx, member):
		swapped = await self.replace_player(ctx, member)
		if swapped:
			await ctx.notice(self.m.gt(
				"{out} backed out and was replaced by {sub}. {sub}, please check in!"
			).format(out=member.mention, sub=swapped.mention))
			await self.refresh(ctx)
		else:
			await self.revert_single(ctx, member)

	async def revert_single(self, ctx, member):
		bot.waiting_reactions.pop(self.message.id, None)
		try:
			await self.message.delete()
		except DiscordException:
			pass
		await ctx.notice("\n".join((
			self.m.gt("{member} has aborted the check-in.").format(member=member.mention),
			self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
		)))
		bot.active_matches.remove(self.m)
		await self.m.queue.revert(ctx, [member], [m for m in self.m.players if m != member])

	async def abort_timeout(self, ctx):
		not_ready = [m for m in self.m.players if m not in self.ready_players]
		if self.message:
			bot.waiting_reactions.pop(self.message.id, None)
			try:
				await self.message.delete()
			except DiscordException:
				pass

		bot.active_matches.remove(self.m)

		await ctx.notice("\n".join((
			self.m.gt("{members} was not ready in time.").format(members=join_and([m.mention for m in not_ready])),
			self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
		)))

		await self.m.queue.revert(ctx, not_ready, list(self.ready_players))
```

- [ ] **Step 2: Verify it parses and the suite still passes**

Run: `python -c "import ast; ast.parse(open('bot/match/check_in.py').read()); print('ok')"`
Expected: `ok`

Run: `ruff check bot/match/check_in.py bot/match/subbing.py`
Expected: no errors.

Run: `pytest tests/ -q`
Expected: PASS (no test imports `check_in.py`'s Discord paths; this confirms nothing broke).

- [ ] **Step 3: Commit**

```bash
git add bot/match/check_in.py
git commit -m "feat(check-in): single-player swap/revert back-out + 1-min warning"
```

---

## Task 4: Live timer field + remove ❌ marker in the check-in embed

**Files:**
- Modify: `bot/match/embeds.py:32-72` (the `check_in` method)

- [ ] **Step 1: Remove the ❌ discarded marker**

In `bot/match/embeds.py`, in the `check_in` method's "Waiting on:" field, change:

```python
			value="\n".join((f" ​ {'❌ ' if p in self.m.check_in.discarded_players else ''}<@{p.id}>" for p in not_ready)),
```

to:

```python
			value="\n".join((f" ​ <@{p.id}>" for p in not_ready)),
```

- [ ] **Step 2: Reword the back-out instruction (no-maps branch)**

Change:

```python
				value=self.m.gt(
					"Please react with {ready_emoji} to **check-in** or {not_ready_emoji} to **abort**!").format(
					ready_emoji=self.m.check_in.READY_EMOJI, not_ready_emoji=self.m.check_in.NOT_READY_EMOJI
				) + "\n​",
```

to:

```python
				value=self.m.gt(
					"Please react with {ready_emoji} to **check-in** or {not_ready_emoji} to **back out**!").format(
					ready_emoji=self.m.check_in.READY_EMOJI, not_ready_emoji=self.m.check_in.NOT_READY_EMOJI
				) + "\n​",
```

- [ ] **Step 3: Reword the back-out instruction (maps branch)**

Change:

```python
					self.m.gt("React with {not_ready_emoji} to **abort**!").format(
						not_ready_emoji=self.m.check_in.NOT_READY_EMOJI
					) + "\n​\nMaps:",
```

to:

```python
					self.m.gt("React with {not_ready_emoji} to **back out**!").format(
						not_ready_emoji=self.m.check_in.NOT_READY_EMOJI
					) + "\n​\nMaps:",
```

- [ ] **Step 4: Add the live timer field before the footer**

Immediately before `embed.set_footer(**self.footer)` in the `check_in` method, insert:

```python
		if self.m.check_in.timeout:
			embed.add_field(
				name=self.m.gt("Check-in ends:"),
				value=f"<t:{self.m.check_in.end_time}:R>",
				inline=False
			)
```

- [ ] **Step 5: Verify parse + lint**

Run: `python -c "import ast; ast.parse(open('bot/match/embeds.py').read()); print('ok')"`
Expected: `ok`

Run: `ruff check bot/match/embeds.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add bot/match/embeds.py
git commit -m "feat(check-in): live countdown timer in embed, drop discarded marker"
```

---

## Task 5: Generalize `Draft.sub_auto` to target any player

**Files:**
- Modify: `bot/match/draft.py:154-192`

- [ ] **Step 1: Rename the parameter and its uses**

In `bot/match/draft.py`, change the signature:

```python
	async def sub_auto(self, ctx, author):
```

to:

```python
	async def sub_auto(self, ctx, out_member):
```

Then in the method body replace the three `author` references:

- `self.m.players.remove(author)` → `self.m.players.remove(out_member)`
- `if author in self.sub_queue:` → `if out_member in self.sub_queue:`
- `self.sub_queue.remove(author)` → `self.sub_queue.remove(out_member)`
- in the notice, `old=author.mention` → `old=out_member.mention`

The state guard (`DRAFT`/`WAITING_REPORT`), candidate pick, rebalance
(`init_teams("matchmaking")`), and final refresh stay unchanged.

- [ ] **Step 2: Verify parse + lint**

Run: `python -c "import ast; ast.parse(open('bot/match/draft.py').read()); print('ok')"`
Expected: `ok`

Run: `ruff check bot/match/draft.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add bot/match/draft.py
git commit -m "refactor(draft): sub_auto targets a given player, not just the caller"
```

---

## Task 6: Route `/subauto [player]` by match state

**Files:**
- Modify: `bot/commands/matches.py:49-51`

- [ ] **Step 1: Replace the `sub_auto` command**

In `bot/commands/matches.py`, replace:

```python
@author_match
async def sub_auto(ctx, match: bot.Match):
	await match.draft.sub_auto(ctx, ctx.author)
```

with:

```python
async def sub_auto(ctx, player: Member = None):
	who = player or ctx.author
	if (match := find(lambda m: m.qc == ctx.qc and who in m.players, bot.active_matches)) is None:
		raise bot.Exc.NotInMatchError(ctx.qc.gt("Specified user is not in an active match."))
	if match.state == bot.Match.CHECK_IN:
		swapped = await match.check_in.replace_player(ctx, who)
		if swapped is None:
			raise bot.Exc.NotFoundError(ctx.qc.gt("There are no available players in the queue to substitute in."))
		await ctx.notice(match.qc.gt("{out} was substituted by {sub}. {sub}, please check in!").format(
			out=who.mention, sub=swapped.mention
		))
		await match.check_in.refresh(ctx)
	else:
		await match.draft.sub_auto(ctx, who)
```

(`Member` and `find` are already imported at the top of the file; `@author_match`
is no longer used here but is still used by other commands, so leave it defined.)

- [ ] **Step 2: Verify parse + lint**

Run: `python -c "import ast; ast.parse(open('bot/commands/matches.py').read()); print('ok')"`
Expected: `ok`

Run: `ruff check bot/commands/matches.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add bot/commands/matches.py
git commit -m "feat(subauto): replace any named player, route check-in vs draft"
```

---

## Task 7: Add the optional `player` arg to the `/subauto` slash command

**Files:**
- Modify: `bot/context/slash/commands.py:533-540`

- [ ] **Step 1: Replace the slash definition**

In `bot/context/slash/commands.py`, replace:

```python
@dc.slash_command(
	name='subauto',
	description='Replace yourself with the next player in queue and rebalance teams by ELO',
	**guild_kwargs
)
async def _sub_auto(
		interaction: Interaction
): await run_slash(bot.commands.sub_auto, interaction=interaction)
```

with:

```python
@dc.slash_command(
	name='subauto',
	description='Replace a player with the next in queue (yourself if no player given)',
	**guild_kwargs
)
async def _sub_auto(
		interaction: Interaction,
		player: Member = SlashOption(
			name="player", description="Player to replace (defaults to you).",
			required=False, default=None, verify=False
		)
): await run_slash(bot.commands.sub_auto, interaction=interaction, player=player)
```

(`Member` and `SlashOption` are already imported/used in this file.)

- [ ] **Step 2: Verify parse + lint**

Run: `python -c "import ast; ast.parse(open('bot/context/slash/commands.py').read()); print('ok')"`
Expected: `ok`

Run: `ruff check bot/context/slash/commands.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add bot/context/slash/commands.py
git commit -m "feat(subauto): optional player arg on the slash command"
```

---

## Task 8: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Lint the whole repo**

Run: `ruff check .`
Expected: no errors.

- [ ] **Step 2: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all pass, including `tests/test_subauto.py`.

- [ ] **Step 3: Confirm config var is fully gone**

Run: `grep -rn "check_in_discard_immediately\|discard_immediately\|discarded_players\|abort_member\|discard_member" bot/`
Expected: no matches.

- [ ] **Step 4: Manual smoke test (record results)**

These need a live bot + Discord and are checked by a human, not asserted in code:
1. Fill a queue so check-in starts → embed shows a live "Check-in ends: in N minutes" countdown.
2. One player hits `⛔` with another player waiting in the queue → that player is swapped out, the queued player is pinged to check in, match continues.
3. One player hits `⛔` with nobody waiting → only that player is dropped, the rest stay queued, channel says it reverted to gathering.
4. `/subauto player:<someone in check-in>` with a queued player available → named player swapped, sub must check in.
5. `/subauto player:<someone in check-in>` with nobody queued → error "no available players", named player stays.
6. `/subauto` (no arg) during draft → unchanged self-sub + rebalance.
7. Let check-in run to ~60s remaining → the 1-minute warning pings the not-ready players exactly once.

- [ ] **Step 5: Final no-op commit guard (only if anything was fixed during verification)**

If steps 1-3 surfaced fixes, commit them:

```bash
git add -A
git commit -m "fix(check-in): verification follow-ups"
```

---

## Self-review notes

- **Spec coverage:** back-out swap/revert (Tasks 3, 4), `/subauto [player]` any-stage/any-player (Tasks 5-7), live timer (Task 4) + `end_time` (Task 3), 1-minute warning (Tasks 1, 3), config removal + dead-code cleanup (Tasks 2, 3). All spec sections map to a task.
- **Type consistency:** `replace_player(ctx, out_member) -> member|None`, `should_warn(frame_time, end_time, already_warned, num_not_ready) -> bool`, `Draft.sub_auto(ctx, out_member)`, and `CheckIn.end_time` are used with identical names/signatures across tasks.
- **Known issue left as-is:** `Draft.sub_for` is still broken during check-in (touches absent teams, calls `refresh()` without `ctx`). Out of scope per the decision to only extend `/subauto`; flagged in the spec.
