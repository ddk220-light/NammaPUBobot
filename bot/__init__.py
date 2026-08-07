# -*- coding: utf-8 -*-
"""The bot package. IT DEFINES NOTHING AND RE-EXPORTS NOTHING, ON PURPOSE.

Two earlier versions of this file are worth knowing about, because both cost
real bugs and both are easy to recreate.

**It held the world.** Six mutable globals -- queue_channels, active_matches,
active_queues, waiting_reactions, bot_ready, bot_was_ready -- writable by
anything that did `import bot`. Phase 1 replaced them with bot/app.py's
Application, constructed once at boot and passed explicitly.

**It held a re-export shelf.** `bot.Exc`, `bot.Match`, `bot.stats`, `bot.Qr`,
and a dozen more, plus the block of side-effect imports that ran every feature
package. Removing the state alone did not fix the import graph, because that
shelf was the real cause: a package's `__init__` runs to completion before any
of its submodules can be reached, so every module in the tree transitively
depended on every feature -- 34 modules in ONE import cycle, worked around by
25 function-local imports.

Two of those re-exports were actively dangerous rather than merely awkward:
`from .stats import stats` and `from .expire import expire` rebound a package
name to an object inside it. That is the exact shadowing that killed audience
predictions for months (`bot.predictions.jobs` resolving to the singleton
rather than the module -- see bot/predictions/__init__.py), sitting unfired in
two more places.

So: import from the module that defines the thing.

	from bot.exceptions import Exceptions as Exc
	from bot.match.match import Match
	from bot.stats import stats

The imports that exist only for their side effects -- ensure_table, job
singletons, slash registration -- are boot wiring and live in bot/bootstrap.py,
which the entrypoint calls explicitly.
"""
