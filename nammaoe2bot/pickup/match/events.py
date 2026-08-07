# -*- coding: utf-8 -*-
"""Match lifecycle events: the domain announces, features subscribe.

A Match used to call `bot.predictions.open_for_match`, `resolve_for_match` and
`void_for_match` directly, and `Draft` called `restart_for_match`; both also
reached into `bot.lobby.watcher`, `bot.team_insights` and
`bot.storyline_payoff`. Eleven function-local imports, all of them the pickup
domain depending on a feature built on top of it.

That direction is wrong regardless of whether it happens to compile. **A match
does not know that betting exists.** It knows that it went live, that its
roster changed, and that it finished — which is all any of those features
actually needed. Everything that wants to hear about it registers here, and the
registration lives in bot/wiring.py so the whole set is readable in one place.

WHAT THIS IS NOT. It is not a general event bus, and it must not grow into one.
There are six events because there are six moments the domain already
announced; adding a seventh means the domain gained a moment, not that a
feature wanted a hook.

ORDER IS PART OF THE CONTRACT. Handlers run in registration order, and two of
the sequences in bot/wiring.py are load-bearing rather than cosmetic — see the
notes there before reordering anything.
"""
from nammaoe2bot.runtime.console import log


class MatchLifecycle:
	"""Subscription table for the six moments a match announces.

	Held on Application, so a handler reaches it the same way it reaches every
	other piece of live state (`self.qc.app.match_events`) and no new global
	appears. Constructed empty; bot/wiring.py fills it once at boot.
	"""

	# Declared up front so a typo in `on("finshed", ...)` fails at wiring time
	# with a name it can print, rather than registering a handler that is never
	# called and reporting nothing at all.
	EVENTS = (
		"teams_posted",     # the teams embed has been sent
		"live",             # teams are final, the match is being played
		"roster_changed",   # a substitution rewrote the teams
		"ending",           # dropped from active_matches, result not yet stored
		"result_recorded",  # a result reached `matches` — NOT the same as finished
		"finished",         # the match itself is over — see wiring.py, order matters
		"cancelled",        # aborted; there will never be a result
	)

	# `result_recorded` and `finished` look redundant and are not.
	# Match.fake_ranked_match — what /report_manual builds for a game played
	# outside the bot — writes a result and never enters finish_match, so it
	# records without finishing. Anything that cares about the RESULT (civ
	# recording) hangs off the first; anything that cares about the MATCH
	# (settlement, storylines) hangs off the second.

	def __init__(self):
		self._handlers = {name: [] for name in self.EVENTS}

	def on(self, event, handler):
		"""Subscribe. `handler` is `async (match, ctx) -> None`.

		Every handler takes both arguments even when it only wants one — a
		uniform signature is what lets wiring.py read as a list of
		subscriptions instead of a list of adapters.
		"""
		if event not in self._handlers:
			raise ValueError(f"{event!r} is not a match lifecycle event: {self.EVENTS}")
		self._handlers[event].append(handler)

	def handlers(self, event):
		"""Subscribers for one event, in registration order. Tests read this;
		nothing in the bot needs it."""
		if event not in self._handlers:
			raise ValueError(f"{event!r} is not a match lifecycle event: {self.EVENTS}")
		return tuple(self._handlers[event])

	async def emit(self, event, match, ctx=None):
		"""Announce, in registration order, and never raise.

		EVERY HANDLER IS ISOLATED. This is not defensive habit — it is the rule
		all six call sites already followed by hand, because none of these
		features may affect whether a match runs or reports: a betting outage
		must not stop a result being recorded, and a storyline that cannot be
		computed is worth strictly less than the match it decorates. A raising
		handler is logged and the ones after it still run.
		"""
		if event not in self._handlers:
			raise ValueError(f"{event!r} is not a match lifecycle event: {self.EVENTS}")
		for handler in self._handlers[event]:
			try:
				await handler(match, ctx)
			except Exception as e:
				name = getattr(handler, "__name__", repr(handler))
				log.error(f"Match lifecycle handler {name} failed on '{event}' "
						  f"for match {getattr(match, 'id', '?')}: {e}")
