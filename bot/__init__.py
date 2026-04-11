# -*- coding: utf-8 -*-

import time

from .main import update_qc_lang, update_rating_system, save_state
from .main import load_state, enable_channel, disable_channel
from .main import remove_players, expire_auto_ready

from .queue_channel import QueueChannel
from .queues.pickup_queue import PickupQueue
from .queues.common import QueueResponses as Qr
from .match.match import Match
from .expire import expire
from .stats import stats
from .stats.noadds import noadds
from .exceptions import Exceptions as Exc
from .context import Context, SlashContext, SystemContext
from . import commands

from . import events
from . import utils


class _TTLReactionDict(dict):
	"""Dict with a TTL sweep for check-in reaction callbacks.

	Backward-compatible with the prior bare `dict` usage — call sites still
	do `bot.waiting_reactions[msg.id] = cb` and `bot.waiting_reactions.pop(msg.id)`,
	both of which route through __setitem__/pop and update the expiry table.

	Why this matters: the check-in flow (bot/match/check_in.py) subscribes a
	callback when the check-in message goes up, and unsubscribes on every
	exit path (success, timeout, abort, discard-all). If any of those paths
	raises before reaching the pop(), the callback stays in this dict
	forever, leaking slowly. Over long uptime (weeks between Railway
	redeploys) the map accumulates dead entries.

	The TTL is 30 minutes — much longer than the longest legitimate check-in
	window (~2 min) so we never sweep a live subscription. The sweep is
	driven from bot/events.py on_think and is O(n) per tick, which is fine
	because n is typically 0-3 at any moment.
	"""

	TTL_SECONDS = 30 * 60

	def __init__(self):
		super().__init__()
		self._expiry = {}

	def __setitem__(self, key, value):
		super().__setitem__(key, value)
		self._expiry[key] = time.time() + self.TTL_SECONDS

	def __delitem__(self, key):
		super().__delitem__(key)
		self._expiry.pop(key, None)

	def pop(self, key, *args):
		self._expiry.pop(key, None)
		return super().pop(key, *args)

	def clear(self):
		super().clear()
		self._expiry.clear()

	def sweep_expired(self, now):
		"""Remove entries whose expiry is before `now`. Returns count removed."""
		expired = [k for k, e in self._expiry.items() if e < now]
		for k in expired:
			super().pop(k, None)
			self._expiry.pop(k, None)
		return len(expired)


bot_was_ready = False
bot_ready = False
queue_channels = dict()  # {channel.id: QueueChannel()}
active_queues = []
active_matches = []
waiting_reactions = _TTLReactionDict()  # {message.id: function}
allow_offline = []  # [user_id]
auto_ready = dict()  # {user.id: timestamp}


def background_context(coro):
	async def wrapper(qc, *args, **kwargs):
		await coro(SystemContext(qc=qc), *args, **kwargs)
	return wrapper
