# -*- coding: utf-8 -*-
"""Boot wiring: the imports that have to HAPPEN, in one place that says so.

Most of this file is `import X` with no name bound, which looks like dead code
and is the opposite. Three kinds of thing only exist because a module was
imported:

* **Schema.** Every feature package declares its own tables with
  `db.ensure_table(...)` at module scope. Nothing calls those declarations;
  importing the package IS the declaration.
* **Job singletons.** `quiz.jobs`, `betting.jobs`, `lobby.jobs`,
  `ingest.jobs` are module-level instances the tick in nammaoe2bot/discord/events.py
  drives. They construct on import.
* **The command surface.** Importing `nammaoe2bot.discord.slash` runs a file
  of `@group.subcommand`-decorated functions, which is what registers all 44
  slash commands with the Discord client.

THIS USED TO BE `bot/__init__.py`, AND THAT IS WHY THE IMPORT ORDER WAS
LOAD-BEARING. A package's `__init__` runs before any of its submodules can be
reached, so a block like this one sitting there meant every module in the tree
had to wait for every feature to finish importing -- and any module that needed
a package listed below itself could not import it at module scope at all. That
is what the 25 function-local `import bot` statements were working around, and
what put 34 modules into a single import cycle. Here, nothing imports this
file except the entrypoint, so the order below is a preference, not a
constraint.

Call `bootstrap()` once, after nammaoe2bot.runtime.database is initialised and before the
Discord client connects.
"""


def bootstrap(app):
	"""Import every module whose import is itself the wiring, then connect the
	features to the domain."""
	# The domain, plus the state snapshot's bot_state table and the channel /
	# queue config factories.
	from nammaoe2bot import state                       # noqa: F401
	from nammaoe2bot.pickup import channel              # noqa: F401

	# Features, each self-contained: its tables, and its job singleton if the
	# tick drives one.
	from nammaoe2bot.features import betting            # noqa: F401
	from nammaoe2bot.features import lobby              # noqa: F401
	from nammaoe2bot.features import quiz               # noqa: F401
	from nammaoe2bot.features.civs import reconcile     # noqa: F401

	# Ingest, and the two layers derived from it.
	from nammaoe2bot import ingest                      # noqa: F401
	from nammaoe2bot import derived                     # noqa: F401
	from nammaoe2bot.derived import classifications     # noqa: F401

	# The Discord front end. Importing this module registers the slash surface.
	from nammaoe2bot.discord import slash               # noqa: F401

	# The event handlers (on_ready, on_message, on_interaction, the tick).
	from nammaoe2bot.discord import events              # noqa: F401

	# Subscribe the features to the match lifecycle. Unlike everything above
	# this is a real call, not an import for its side effect: a Match announces
	# that it went live, changed roster or finished, and betting, the lobby
	# watcher and the storylines listen. Nothing under pickup/ imports them.
	from nammaoe2bot.wiring import wire_lobby_to_betting, wire_match_lifecycle
	wire_match_lifecycle(app)

	# And the one feature-to-feature introduction. It is currently configured
	# in observation mode, which explicitly leaves betting timer-only while the
	# lobby socket's real launch/cancellation events are captured.
	wire_lobby_to_betting()
