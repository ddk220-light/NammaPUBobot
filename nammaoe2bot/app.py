# -*- coding: utf-8 -*-
"""The application object: everything that used to be a module-level global.

`bot/__init__.py` held six mutable globals — queue_channels, active_matches,
active_queues, waiting_reactions, bot_ready, bot_was_ready — writable by any
module that did `import bot`. That single fact is what forced 65 function-local
imports across the codebase: importing at module scope deadlocked, because the
module holding the world also re-exported half of everything that used it.

An Application is constructed ONCE, at boot, and passed explicitly to whatever
needs it.

THERE IS DELIBERATELY NO MODULE-LEVEL INSTANCE AND NO get_app() ACCESSOR.
A global handle here would be the same coupling with a nicer name: any module
could reach the world again without declaring that it depends on it, and the
import cycles would come straight back. If passing it somewhere is awkward,
that awkwardness is the design telling you the dependency is in the wrong
direction — the fix is to move the code, not to add an accessor.
"""
import time

from nammaoe2bot.pickup.match.events import MatchLifecycle


class TTLReactionDict(dict):
	"""Dict with a TTL sweep for check-in reaction callbacks.

	Call sites do `waiting_reactions[msg.id] = cb` and `.pop(msg.id)`, both of
	which route through __setitem__/pop and update the expiry table.

	Why this exists: the check-in flow (nammaoe2bot/pickup/match/checkin.py) subscribes a
	callback when the check-in message goes up and unsubscribes on every exit
	path — success, timeout, abort, discard-all. If any of those raises before
	reaching the pop(), the callback stays here forever, leaking slowly. Over
	long uptime (weeks between Railway redeploys) the map accumulates dead
	entries.

	The TTL is 30 minutes, far longer than the longest legitimate check-in
	window (~3 min), so a live subscription is never swept. The sweep runs from
	the tick and is O(n) per pass, which is fine because n is typically 0-3.
	"""

	TTL_SECONDS = 30 * 60

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._expiry = {}

	def __setitem__(self, key, value):
		super().__setitem__(key, value)
		self._expiry[key] = time.time() + self.TTL_SECONDS

	def pop(self, key, *args):
		self._expiry.pop(key, None)
		return super().pop(key, *args)

	def sweep_expired(self, now):
		"""Drop entries past their TTL. Returns how many went."""
		expired = [k for k, at in self._expiry.items() if at <= now]
		for k in expired:
			super().pop(k, None)
			self._expiry.pop(k, None)
		return len(expired)


class Application:
	"""The bot's live state, held in one place and passed explicitly.

	Attribute names are shorter than the globals they replace (`channels`, not
	`queue_channels`) because `app.channels` already says what it is; the old
	names were carrying the namespace on their back.
	"""

	def __init__(self, client):
		self.client = client
		self.channels = {}            # {channel_id: QueueChannel}
		self.active_queues = []
		self.active_matches = []
		self.waiting_reactions = TTLReactionDict()   # {message_id: callback}
		self.ready = False
		self.was_ready = False
		# Empty until nammaoe2bot/wiring.py subscribes the features. A Match announces
		# through this rather than importing betting, the lobby watcher or the
		# storyline builders — see nammaoe2bot/pickup/match/events.py.
		self.match_events = MatchLifecycle()

	async def remove_players(self, *users, reason=None):
		"""Drop these users from every queue that currently holds anyone.

		It lived in nammaoe2bot/state.py as a free function taking `app` as its first
		argument, which is a method with extra steps — and an expensive one:
		main.py is also the state-snapshot module, so check_in.py and draft.py
		importing it for this one call put the whole snapshot chain into their
		import graph and closed a cycle (main -> pickup_queue -> match ->
		check_in -> main). Reaching it through the object whose list it walks
		costs no import at all.
		"""
		for qc in set(q.qc for q in self.active_queues):
			await qc.remove_members(*users, reason=reason)
