import os
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
LOGS_DIR = os.path.join(DATA_DIR, 'logs')


def _ensure_log_dir():
	os.makedirs(LOGS_DIR, exist_ok=True)


def log_channel_message(message):
	"""Log every channel message to data/logs/channel_messages.log."""
	_ensure_log_dir()
	ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
	author = f"{message.author.name}#{message.author.discriminator}"
	content = message.content[:200].replace('\n', ' ') if message.content else ''
	embed_count = len(message.embeds) if message.embeds else 0
	line = f"[{ts}] {author} ({message.author.id}): {content}"
	if embed_count:
		line += f" [{embed_count} embed(s)]"
	line += '\n'

	path = os.path.join(LOGS_DIR, 'channel_messages.log')
	with open(path, 'a', encoding='utf-8') as f:
		f.write(line)


def log_bot_message(message, bot_name):
	"""Log Pubobot/LobbyBOT messages in detail to data/logs/bot_messages.log."""
	_ensure_log_dir()
	ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
	lines = [f"[{ts}] {bot_name} ({message.author.id}) msg_id={message.id}"]

	if message.content:
		lines.append(f"  content: {message.content}")

	for i, embed in enumerate(message.embeds or []):
		lines.append(f"  embed[{i}]:")
		if embed.title:
			lines.append(f"    title: {embed.title}")
		if embed.description:
			for desc_line in embed.description.split('\n'):
				lines.append(f"    desc: {desc_line}")
		for field in (embed.fields or []):
			lines.append(f"    field '{field.name}': {field.value}")

	path = os.path.join(LOGS_DIR, 'bot_messages.log')
	with open(path, 'a', encoding='utf-8') as f:
		f.write('\n'.join(lines) + '\n\n')
