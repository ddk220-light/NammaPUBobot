# NammaAoe2Bot — architecture restructure

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development
> to implement this plan phase by phase. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Take a codebase that is still upstream PUBobot2's skeleton wearing this
fork's features, and make it this fork's own — explicit dependencies instead of
global state, layered packages instead of one flat `bot/`, and no trace of the
old bot's name outside the licence attribution.

**Naming, decided:**

| Thing | Value |
|---|---|
| Bot display name | **NammaAoe2Bot** |
| Python package root | `nammaoe2bot` (lowercase — import convention) |
| Entrypoint | `python -m nammaoe2bot` |

**Tech stack:** unchanged. Python 3.11, nextcord, aiomysql, MySQL, Railway.
This is a restructure, not a rewrite — behaviour must be identical throughout.

---

## Why this is worth doing

Measured, not assumed:

| Signal | Value |
|---|---|
| Lines in files upstream originally created | 7,131 of 29,975 (**23%**) — and they are the skeleton: `cfg_factory`, `queue_channel`, `match`, `pickup_queue`, `rating`, the MySQL adapter, `client`, `config`, `context` |
| Modules doing a bare `import bot` | **26** |
| Reads/writes of `bot.queue_channels` / `active_matches` / `active_queues` | **78** |
| **Function-local `bot` imports (circular-dependency workarounds)** | **65** |
| Test code | 29,107 lines / 100 files — **more than the bot itself** (26,373) |

That last-but-one number is the diagnosis. 65 places had to defer an import
inside a function because importing at module scope would deadlock. Every one is
the same root cause: `bot/__init__.py` holds mutable global state *and* re-exports
half the codebase, so everything depends on everything.

**Renaming files without fixing that produces a relabelled version of the same
architecture.** Phase 1 exists because of this, and it goes first.

---

## Global Constraints

Binding on every task in every phase.

- **Behaviour must not change.** This is a restructure. Any behavioural change is
  a defect, including changed log text, embed copy, or command descriptions —
  except where a task explicitly says otherwise.
- **The bot serves a live community with real gold balances.** `gold_ledger` is
  append-only and `balance == SUM(ledger.amount)` is an invariant. No phase may
  touch `bot/predictions/gold.py`'s transaction boundaries.
- **The wire format is frozen.** Interaction `custom_id` prefixes — `quiz:`,
  `bet:`, `betcancel:` — are written into live Discord messages. There is an open
  quiz post right now carrying `quiz:{post}:ans:{i}`. Do not change these strings.
  If a router moves, it keeps parsing the same prefixes.
- **`cfg_name` is a database value, not a code identifier.** `channel_settings`
  and `queue_settings` store `qc_config` / `pq_config`, and `CfgFactory.spawn`
  selects on it. Renaming a factory without migrating those two rows makes the
  bot boot with blank config and lose the channel settings and the `namma_nomad`
  queue. See Phase 3, Task 3.1.
- **`PUBOBOT_USER_ID` keeps its name.** It identifies the *actual* Pubobot bot in
  the guild, which `bot/elo_sync.py` reads ELO from. Renaming it makes it wrong.
- **GPL-3 attribution stays.** `LICENSE` and Leshaka's copyright remain; files
  derived from upstream remain GPL. Renaming the bot is fine, removing the
  attribution is not. Add a "derived from PUBobot2 by Leshaka" line to README
  credits and keep it there.
- **Tabs, not spaces**, in every file that already uses tabs. Match the file you
  edit; never mix within one file.
- **`ruff check .` clean and the full suite green at the end of every task**, not
  every phase. A task that leaves the tree red is not done.
- **No phase merges to main as one commit.** Each task commits separately so a
  bisect can find a behaviour change.

---

## Phase 0 — the naming that costs nothing

No structural change, no import moves, no risk. Ship this first so the bot stops
introducing itself as someone else's software while the rest is in flight.

### Task 0.1: Stop presenting as PUBobot2

**Files:** `start.py`, Railway env

- [ ] **Step 1: Set the Railway env vars.** `STATUS` is unset, so the Discord
      presence currently reads `PUBobot2`. Set `STATUS=NammaAoe2Bot`. `HELP` and
      `COMMANDS_URL` are also unset but their commands are gone, so remove those
      two from `start.py`'s template and from `core/config.py` rather than
      setting them.
- [ ] **Step 2: Change the defaults too**, so a fresh deploy without env vars is
      still correct: `start.py`'s `status=os.environ.get("STATUS", "NammaAoe2Bot")`.
- [ ] **Step 3:** `ruff check . && pytest tests/ -q`
- [ ] **Step 4:** Commit — `chore(naming): the bot presents as NammaAoe2Bot`

### Task 0.2: Rename the exception hierarchy

**Files:** `bot/exceptions.py` + ~10 call sites

`PubobotException` is the base class every handler catches.

- [ ] **Step 1:** Rename `PubobotException` → `BotException` in
      `bot/exceptions.py`.
- [ ] **Step 2:** Update every `bot.Exc.PubobotException` reference
      (`bot/events.py`, `bot/context/slash/commands.py`,
      `bot/queues/pickup_queue.py`, `bot/context/`).
- [ ] **Step 3:** `grep -rn "PubobotException" bot/ core/ tests/` returns nothing.
- [ ] **Step 4:** `ruff check . && pytest tests/ -q`
- [ ] **Step 5:** Commit — `chore(naming): PubobotException -> BotException`

### Task 0.3: Remove the last dead module and functions

**Files:** `bot/changelog.py` (delete), ~28 functions across `bot/`, `core/`

- [ ] **Step 1:** Delete `bot/changelog.py` — nothing has referenced it since its
      command was removed.
- [ ] **Step 2:** Re-run the orphan sweep and remove the ~423 lines of unreferenced
      public functions. **Verify each by hand first**: `@dc.event` handlers are
      registered by decorator and look orphaned to a static scan. Anything
      registered by decorator or dispatched by string stays.
- [ ] **Step 3:** `ruff check . && pytest tests/ -q`
- [ ] **Step 4:** Commit — `chore: remove the last unreferenced modules and functions`

---

## Phase 1 — dismantle the god-module

**This is the phase that matters.** Everything after it is moving boxes.

Files stay exactly where they are throughout this phase. The tests keep passing
against familiar paths while the hard change happens underneath — which is the
whole point of doing it before Phase 2.

### The target

`bot/__init__.py` currently owns six mutable globals:

| Global | Refs | Mutating |
|---|---|---|
| `queue_channels` | 38 | 8 |
| `active_matches` | 31 | 7 |
| `waiting_reactions` | 18 | 11 |
| `active_queues` | 9 | 4 |
| `bot_ready` | 6 | 4 |
| `bot_was_ready` | 3 | 1 |

Replace with one `Application` object, constructed once at boot and passed
explicitly. Not a singleton with a global accessor — that is the same thing with
extra steps.

```python
# nammaoe2bot/app.py  (lives at bot/app.py during this phase)
class Application:
	"""Everything that was a module-level global in bot/__init__.py.

	Passed explicitly. There is deliberately no module-level instance and no
	get_app() accessor: a global handle to this object would recreate exactly
	the coupling it exists to remove, and the 65 function-local `import bot`
	statements in the old design are what that coupling cost.
	"""

	def __init__(self, client):
		self.client = client
		self.channels: dict[int, QueueChannel] = {}
		self.active_queues: list = []
		self.active_matches: list = []
		self.waiting_reactions = TTLReactionDict()
		self.ready = False
		self.was_ready = False
```

### Task 1.1: Introduce Application, wire it at boot

**Files:** Create `bot/app.py`; modify `PUBobot2.py`, `bot/events.py`

- [ ] **Step 1: Write the failing test** — `tests/test_app.py`:

```python
def test_application_starts_with_empty_state():
	from bot.app import Application
	app = Application(client=object())
	assert app.channels == {}
	assert app.active_matches == []
	assert app.active_queues == []
	assert app.ready is False


def test_application_has_no_module_level_instance():
	"""A global handle would recreate the coupling this class removes."""
	import bot.app as m
	assert not any(isinstance(v, m.Application) for v in vars(m).values())
	assert not hasattr(m, "get_app")
```

- [ ] **Step 2:** Run it, watch it fail with `ModuleNotFoundError`.
- [ ] **Step 3:** Write `bot/app.py` as above. Move `_TTLReactionDict` into it
      from `bot/__init__.py` (keep the docstring — it documents a real leak).
- [ ] **Step 4:** Construct one `Application` in `PUBobot2.py` at boot and hand it
      to the event registration. **Do not remove the old globals yet** — both
      exist for this task, so nothing breaks mid-phase.
- [ ] **Step 5:** `pytest tests/test_app.py -v` passes; full suite still green.
- [ ] **Step 6:** Commit — `refactor(app): introduce Application, the explicit state holder`

### Task 1.2 – 1.7: Convert one global per task

One global per task, in ascending order of blast radius so the pattern is proven
on the small ones first:

| Task | Global | Refs |
|---|---|---|
| 1.2 | `bot_was_ready`, `bot_ready` | 9 |
| 1.3 | `active_queues` | 9 |
| 1.4 | `waiting_reactions` | 18 |
| 1.5 | `active_matches` | 31 |
| 1.6 | `queue_channels` | 38 |
| 1.7 | delete the globals from `bot/__init__.py` | — |

For each, the same shape:

- [ ] **Step 1:** Find every reference: `grep -rn "bot\.<name>" bot/ core/`
- [ ] **Step 2:** Thread `app` to each call site. Where a class needs it (`Match`,
      `QueueChannel`, `PickupQueue`), store it as `self.app` at construction —
      **do not** reach for a global inside a method.
- [ ] **Step 3:** Delete that global from `bot/__init__.py`.
- [ ] **Step 4:** `grep -rn "bot\.<name>"` returns nothing.
- [ ] **Step 5:** `ruff check . && pytest tests/ -q` — full suite, every task.
- [ ] **Step 6:** Commit — `refactor(app): <name> moves onto Application`

**`active_matches` (1.5) is the risky one** and deserves reading before writing.
`bot/predictions/` depends on precise timing: `finish_match` drops the match from
`active_matches` *before* settlement runs, which is exactly why
`prediction_bets.is_player` is captured at press time and why `PredictionJobs._run`
is the only re-runner. Four files in `bot/predictions/` document this in comments.
**Preserve the drop order exactly.** If it changes, a crash mid-payout stops being
recoverable and the resume sweep silently stops working — with the ledger and the
balance cache still agreeing, so `reconcile()` will not see it.

### Task 1.8: Collapse the circular-import workarounds

**Files:** the 65 function-local `bot` imports

- [ ] **Step 1:** `grep -rnE "^\s+(from bot[\. ]|import bot$)" bot/ core/` — list them.
- [ ] **Step 2:** For each, try hoisting to module scope. With state out of
      `bot/__init__.py`, most cycles should be gone.
- [ ] **Step 3:** Any that still cycle mark a genuine dependency inversion —
      **record them in the plan file rather than forcing them**; they are Phase 2's
      input, and they tell you where the layer boundary actually belongs.
- [ ] **Step 4:** `ruff check . && pytest tests/ -q`
- [ ] **Step 5:** Commit — `refactor: hoist the deferred imports the god-module forced`

**Phase 1 exit criteria:** `bot/__init__.py` holds no mutable state; a `grep` for
the six names returns only `app.` attribute access; the deferred-import count is
reported, with any survivors documented.

### Phase 1 outcome — measured, and it changes Phase 2

All six globals are gone; `bot/__init__.py` is 31 lines of re-exports holding
nothing mutable. Deferred imports went from **65 to 62** — and that small number
is the finding, not a disappointment.

**Removing the state was necessary but not sufficient.** The remaining cycles are
not caused by shared state at all; they are caused by `bot/__init__.py`'s
re-export block and the ORDER it imports in:

```
main → queue_channel → queues → match → expire → stats → exceptions →
context → commands → events → utils → civ_reconcile → lobby → quiz →
predictions → replay_stats → classifications → derived
```

**25 deferred imports are provably forced by that order** — a module needs a
package that loads later in the sequence, so importing it at module scope would
fail. They fall into three groups, and each has a different Phase 2 answer:

| Group | Count | Why | Phase 2 answer |
|---|---|---|---|
| `bot/commands/*` → `quiz`, `predictions`, `lobby`, `derived` | 10 | handlers load at position 8, their features at 12-17 | **Dissolved.** Task 2.9 folds each handler into the feature that owns it, so there is no cross-package edge left to defer. |
| `bot/match/*`, `bot/events.py` → `predictions`, `lobby`, `quiz` | 11 | the domain and the tick reaching into features | **A real inversion, not an ordering accident.** A match should not know that betting exists; it should announce that it finished and let a feature subscribe. This is the one place Phase 2 should change a dependency direction rather than move a file. |
| `replay_stats` → `derived` | 2 | ingest loads before the layer it feeds | **Ordering only** — correct direction, fixed by the `ingest/` → `derived/` split. |

The middle row is the substantive discovery of this phase: `Match.finish_match`
calls into `bot.predictions`, and `Draft.sub_for`/`sub_auto` call
`restart_for_match`. Those are the domain depending on a feature. Phase 2 should
introduce a match-lifecycle event the betting and lobby features subscribe to,
rather than relocating the call.

---

## Phase 2 — layer the packages

Only now do files move. With dependencies explicit, this is mechanical.

### Target structure

```
nammaoe2bot/
  __init__.py           version only — never state
  __main__.py           python -m nammaoe2bot
  app.py                the Application from Phase 1

  runtime/              process concerns
    config.py  logging.py  database/  migrations.py  registry.py

  discord/              the Discord adapter
    client.py  commands/  interactions.py  scheduler.py

  pickup/               THE DOMAIN — the pickup game itself
    channel.py          was bot/queue_channel.py
    queue.py            was bot/queues/pickup_queue.py
    match/
      lifecycle.py      was match/match.py
      checkin.py
      substitution.py   was match/draft.py — it no longer drafts
      embeds.py
    rating/             was bot/stats/rating.py + the rating systems

  features/             each self-contained, each owns its own commands
    quiz/  betting/  lobby/  scouting/  civs/  identity/

  ingest/               was bot/replay_stats/
  derived/              unchanged conceptually
  web/                  was bot/web.py + web_page.html
```

**Names that change because they are wrong**, not for the sake of it:
`predictions` → `betting` (it is a gold economy, not a forecast);
`draft.py` → `substitution.py` (there is no draft); `bot/commands/` dissolves
into the feature that owns each handler; `bot/context/` collapses (it abstracted
two front-ends, one of which is now `++`/`--` only). `match.py`, `rating.py`,
`queue.py` keep their names — they are already right, and renaming them costs
every `git blame` for nothing.

### Task sequence

**The order changed once Phase 1 reported.** The original sequence moved files
first and left `bot/__init__.py`'s re-export block for last. That is backwards.
Every `bot.Exc`, `bot.stats`, `bot.Match` in the tree — 250 attribute accesses —
resolves through that block, so moving files underneath it means rewriting each
access twice: once when its module moves, once when the block dies. Worse, the
block is *why* the 25 deferred imports exist, so moving files while it stands
carries the cycles into the new tree and invites re-creating them there.

So: **dissolve the module first, then move files.** The dissolve is the only
part that needs judgement; once it lands, every remaining task is a path rename
a script can do and `tests/test_import_graph.py` can verify.

- [ ] 2.1 dissolve the re-export module — `bot.X` becomes a real import
- [ ] 2.2 the match-lifecycle inversion — a composition root, not a deferred import
- [ ] 2.3 `core/` → `nammaoe2bot/runtime/`
- [ ] 2.4 `bot/queue_channel.py`, `queues/`, `match/`, `stats/`, `expire.py` → `pickup/`
- [ ] 2.5 `bot/quiz/`, `predictions/`, `lobby/` and the loose feature modules → `features/`
- [ ] 2.6 `bot/replay_stats/` → `ingest/`; `bot/derived/` + `classifications/` → `derived/`
- [ ] 2.7 `core/client.py`, `bot/context/`, `bot/events.py`, `bot/commands/` → `discord/`
- [ ] 2.8 `bot/web.py`, `bot/web_page.html` → `web/`
- [ ] 2.9 `bot/` is empty — `app.py`, `bootstrap.py`, `wiring.py`, `main.py` take
      their places at the package root, and the tests follow the code

Each task: `git mv` (preserves history — never delete-and-create), rewrite
imports, `ruff check .`, full suite, commit.

### Task 2.1: dissolve the re-export module

**Files:** `bot/__init__.py` (emptied), `bot/bootstrap.py` (new), plus every
module that reaches through `bot.`

`bot/__init__.py` does two unrelated jobs and both have to move:

1. **Re-exports** — `bot.Exc`, `bot.Match`, `bot.stats`, `bot.Qr`, … Each
   becomes a direct import in the module that uses it. Two of these were
   actively dangerous: `from .stats import stats` and `from .expire import
   expire` rebind a package/module name to the object inside it, which is
   exactly the shadowing that killed the predictions feature for months
   (`bot.predictions.jobs` — see `bot/predictions/__init__.py`). A direct
   `from bot.stats import stats` in the consuming module has no such ambiguity.
2. **Side-effect imports** — the `from . import quiz / predictions / lobby / …`
   block that exists so `ensure_table` runs and the job singletons construct.
   That is *boot wiring*, not package definition, and putting it in
   `__init__.py` is what makes the import ORDER load-bearing. It moves to
   `bot/bootstrap.py`, called explicitly from the entrypoint.

- [ ] **Step 1:** For each re-exported name, add the real import to every module
      that uses it and drop the `import bot` where it becomes unused.
- [ ] **Step 2:** Move the side-effect block to `bot/bootstrap.py` with a
      docstring naming what each import is there for; call it from `PUBobot2.py`
      before the client starts.
- [ ] **Step 3:** `bot/__init__.py` keeps a docstring and nothing else.
- [ ] **Step 4:** `grep -rn '\bbot\.[A-Z]' bot/ core/` returns nothing.
- [ ] **Step 5:** `ruff check . && pytest tests/ -q`
- [ ] **Step 6:** Commit — `refactor: dissolve the re-export module`

#### Task 2.1 outcome

**34 modules in one import cycle → zero cycles.** `tests/test_import_cycles.py`
now resolves the module-level import graph statically and fails on any
strongly-connected component. It is the guard that makes the rest of this
restructure safe without a running bot: conftest stubs `core.*` and `nextcord`
is not installed, so nothing in the suite ever executes the real import graph,
and a circular import is a boot crash rather than a test failure.

Only 20 files actually reached through the shelf (a raw grep suggested 250; most
were prose). Three defects fell out of the conversion:

* **`PUBobot2.py` called `save_state()` with no argument**, twice. Phase 1 gave
  it an `app` parameter and updated `bot/events.py`'s callers but not the
  entrypoint's. One site is inside a `try/except Exception` that logged and
  moved on; the other is the SIGTERM handler, so **every Railway redeploy
  raised TypeError instead of writing the snapshot** — the exact failure the
  function's own docstring exists to prevent.
* **`bot/commands/admin.py` shadowed the noadds tracker with its own handler.**
  `async def noadds(ctx): data = await noadds.get_noadds(ctx)` — the name
  resolved to the function, not the singleton. It only worked because
  `bot.noadds` reached the shelf. Renamed to `show_noadds`, matching
  `show_queues` next to it.
* **`bot/context/__init__.py` re-exported `SlashContext`**, so
  `from bot.context.context import SystemContext` ran the entire slash command
  surface — which imports `QueueChannel`, which imports `SystemContext`. Both
  context packages now re-export nothing; `QueueChannel` is a `TYPE_CHECKING`
  annotation.

`remove_players` moved from `bot/main.py` to `Application`. It took `app` as its
first argument, which is a method with extra steps — and an expensive one:
`main.py` is also the state-snapshot module, so `check_in.py` and `draft.py`
importing it for that one call closed `main → pickup_queue → match → check_in →
main`.

**What the deferred imports are now.** 63 remain, and the count is beside the
point: none of them is still *required*. Feeding every one of them into the
cycle detector as if hoisted leaves **two** cycles in `bot/`, and only one is
real — `derived ↔ replay_stats ↔ post_game`. That one is Task 2.6's problem and
bigger than the plan assumed: `derived.game_stats` reads `replay_stats.card_scoring`
while `replay_stats.store` writes `derived.game_stats`, so it is a genuine
two-way dependency, not the one-way ordering slip recorded after Phase 1.

### Task 2.2: the match-lifecycle inversion

**Files:** `bot/match/events.py` (new), `bot/wiring.py` (new), `bot/app.py`,
`bot/match/match.py`, `bot/match/draft.py`

This is the eleven deferred imports Phase 1 measured, and the only place in
Phase 2 that changes a dependency direction rather than a path. `Match` calls
`bot.predictions.open_for_match`, `resolve_for_match`, `void_for_match`;
`Draft` calls `restart_for_match`; both reach into `bot.lobby.watcher`,
`bot.team_insights` and `bot.storyline_payoff`. **A match should not know that
betting exists.**

`MatchLifecycle` — a dispatcher on `Application`, so it is reached the same way
every other piece of application state is (`self.qc.app.match_events`) and no
new global appears. Five events, matching the five points the domain currently
reaches out from:

| Event | Emitted | Subscribers, in order |
|---|---|---|
| `teams_posted` | end of `final_message` | team insights |
| `live` | end of `start_waiting_report` | lobby watcher start, betting open |
| `roster_changed` | `sub_for`, `sub_auto` | betting restart |
| `ending` | `finish_match`, after the drop from `active_matches` | lobby watcher stop |
| `finished` | `finish_match`, **after** stats registration | storyline payoff, betting settle |
| `cancelled` | `cancel`, after the drop | lobby watcher stop, betting void |

**Two ordering facts are load-bearing and must survive verbatim.** `finished`
fires after `bot.stats` has written the `matches` row, because
`store.unsettled_books` JOINs on it — that join is the entire resume mechanism.
And `ending` fires separately from `finished` only because the watcher stop
currently runs *before* stats; collapsing them would move it after.

Guards (`ranked`, `predictions_enabled`) move from the emit site into the
subscriber. The domain announces unconditionally; the feature decides whether
it cares. Every handler is dispatched inside a try/except that logs — which is
what all six sites already do by hand, and what all four `*_for_match`
functions already do internally.

`bot/wiring.py` is the composition root: the one module that imports both the
domain and the features, and the one place the subscription order is written
down. Nothing under `pickup/` may import a feature after this task.

- [ ] **Step 1:** Write `tests/test_match_lifecycle.py`: registration order is
      dispatch order; a raising handler does not stop the ones after it; every
      event name in `wiring` exists on the dispatcher (a typo'd `on()` would
      otherwise silently never fire).
- [ ] **Step 2:** `bot/match/events.py` — `MatchLifecycle` with `on`/`emit`.
- [ ] **Step 3:** `Application` gains `self.match_events = MatchLifecycle()`.
- [ ] **Step 4:** Replace the six call sites with `emit`; write `bot/wiring.py`.
- [ ] **Step 5:** Update `tests/test_draft_substitutions.py` and
      `tests/test_predictions_wiring.py` — both pin the old call shape.
- [ ] **Step 6:** `ruff check . && pytest tests/ -q`
- [ ] **Step 7:** Commit — `refactor(pickup): the domain announces, features subscribe`

### Tasks 2.3 – 2.9: the moves

Mechanical once 2.1 and 2.2 land. Each one: `git mv`, rewrite the import paths
across `bot/`, `core/`, `utils/`, `tests/` and the two root scripts, then
`ruff check . && pytest tests/ -q`. `tests/test_import_graph.py` resolves every
repo-internal import statically, so a missed rewrite fails the suite rather
than waiting for a runtime `ModuleNotFoundError` — it is the safety net that
makes a restructure of this size safe without a running bot. Update its
`_PACKAGES` tuple in 2.3, when the new root first exists.

---

## Phase 3 — the couplings

Where a mistake is silent rather than loud. Small, and none of it is optional.

### Task 3.1: Migrate `cfg_name`

- [ ] Write migration `00N_config_factory_rename`: `UPDATE channel_settings SET
      cfg_name='channel_config' WHERE cfg_name='qc_config'` and `UPDATE
      queue_settings SET cfg_name='queue_config' WHERE cfg_name='pq_config'`.
- [ ] Rename the factories to match, in the same commit.
- [ ] **Verify against a copy of production, not an empty database.** The failure
      mode is a clean boot with blank config, which an empty test DB cannot show.

### Task 3.2: Entrypoint

- [ ] `PUBobot2.py` → `nammaoe2bot/__main__.py`; `start.py` execs
      `python -m nammaoe2bot`; update `Dockerfile`, `railway.toml`, `ruff.toml`
      (its `"PUBobot2.py"` per-file ignore), CI.

### Task 3.3: Wire-format compatibility

- [ ] Confirm `quiz:`, `bet:`, `betcancel:` parse identically after the router
      moves. Add a test that pins the exact strings — the failure is a live
      message whose buttons stop responding, with no error anywhere.

---

## Phase 4 — tests and docs

- [ ] 4.1 Fix the ~20 test files hard-coding source paths and the 8 parsing source
      with `ast`. **These break progressively through Phase 2** — fix each as its
      package moves, not in a batch at the end, or the safety net is down for the
      whole restructure.
- [ ] 4.2 `CLAUDE.md` rewritten around the new structure.
- [ ] 4.3 `README.md`, `COMMANDS.md`, `RAILWAY_SETUP.md` renamed and re-pathed;
      GPL attribution to Leshaka retained in credits.
- [ ] 4.4 Repo rename `NammaPUBobot` → `NammaAoe2Bot` (GitHub redirects the old
      URL, but update the Railway source and any local clones).

---

## Risks

| Risk | Mitigation |
|---|---|
| **`cfg_name` migration missed** → bot boots with blank config, loses the queue definition | Task 3.1, verified against a production copy. The symptom is a *clean* boot, not a crash. |
| **`active_matches` timing changes** → prediction settlement silently stops being recoverable | Task 1.5 preserves the drop order; the four `bot/predictions/` comments explain why it matters. |
| **Test suite down mid-restructure** | Phase 1 moves no files. Phase 2 fixes tests per package, not in a batch. |
| **Behavioural drift disguised as a move** | Every task commits separately so `git bisect` works; behaviour changes are defects. |
| **`git mv` vs delete+create** loses blame on 30k lines | Always `git mv`. |
| Deploying mid-restructure | Every task leaves the tree deployable; ship Phase 0 immediately and the rest when quiet. |

## Not in scope

- Dropping `player_prefs`, `player_phrases`, `douche_log` or the five dead
  `quiz_settings` columns — destructive, separate decision.
- The retention sweeper's `DRY_RUN = True`.
- The 4 open `identity_conflicts`.
- Rewriting any feature. This is a restructure; behaviour is frozen.
