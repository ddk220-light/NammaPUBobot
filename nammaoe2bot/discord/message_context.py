from __future__ import annotations

from nextcord import Message, Embed

from typing import TYPE_CHECKING

# Annotation only. Importing QueueChannel for real closes a cycle -- a channel
# builds contexts, so queue_channel imports this module -- and the name is
# never touched at runtime here: `from __future__ import annotations` leaves
# every annotation in this file as a string.
if TYPE_CHECKING:
	from nammaoe2bot.pickup.channel import QueueChannel
from nammaoe2bot.runtime.utils import error_embed, ok_embed

from .context import Context


class MessageContext(Context):
	""" Context for plain text-message commands.

	The full text-command system was removed in Layer 5 (slash-only). This
	minimal context was restored to support the `++` / `--` shorthand only —
	it lets the existing add/remove command handlers reply to the channel. """

	def __init__(self, qc: QueueChannel, message: Message):
		self.message = message
		super().__init__(qc, message.channel, message.author)

	async def reply(self, content: str = None, embed: Embed = None):
		await self.message.reply(content=content, embed=embed)

	async def notice(self, content: str = None, embed: Embed = None):
		await (self.message.thread or self.message.channel).send(content=content, embed=embed)

	async def error(self, *args, **kwargs):
		await self.message.reply(embed=error_embed(*args, **kwargs))

	async def success(self, *args, **kwargs):
		await self.message.reply(embed=ok_embed(*args, **kwargs))
