"""Bounded upstream-bot observability.

Railway already retains stdout.  The old implementation duplicated every
selected Discord message into an unbounded file in the container, retaining
message bodies and embed values while providing no durable benefit.  Keep the
event evidence needed to correlate Pubobot/LobbyBOT behavior, but log only
fixed-size metadata through the normal logger.
"""
from nammaoe2bot.runtime.console import log


def log_channel_message(message):
	"""Compatibility no-op; general channel-message capture is disabled."""
	return False


def log_bot_message(message, bot_name):
	"""Record one redacted, constant-size correlation line on Railway stdout."""
	embeds = list(getattr(message, "embeds", None) or ())
	fields = sum(len(getattr(embed, "fields", None) or ()) for embed in embeds)
	log.info(
		"UPSTREAM_BOT_MESSAGE "
		f"bot={str(bot_name)[:24]} "
		f"author_id={getattr(getattr(message, 'author', None), 'id', 0)} "
		f"channel_id={getattr(getattr(message, 'channel', None), 'id', 0)} "
		f"message_id={getattr(message, 'id', 0)} "
		f"content_chars={len(getattr(message, 'content', '') or '')} "
		f"embeds={len(embeds)} fields={fields}")
	return True
