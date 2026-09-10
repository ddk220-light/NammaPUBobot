"""One public card, numbered buttons, and the final team-by-team summary."""
import nextcord
from .picking import RANDOM


def card(match_id, state):
	closed = state['status'] != 'open'
	lines = []
	for i, civ in enumerate(state['options']):
		owner = next((uid for uid, choice in state['picks'].items() if choice == i), None)
		lines.append(f'**{i + 1}. {civ}**' + (f' — <@{owner}>' if owner else ''))
	lines.append('**13. 🎲 Random** — everyone may choose this')
	for team in (0, 1):
		lines += ['', f'**Team {team + 1}**']
		for player in state['roster']:
			if player['team'] != team:
				continue
			uid = str(player['id'])
			choice = state['picks'].get(uid)
			label = 'Waiting to pick' if choice is None else (
				'Random' if choice == RANDOM else state['options'][choice])
			if uid in state['timed_out']:
				label += ' (timed out)'
			lines.append(f'<@{uid}> → {label}')
	if state['status'] == 'invalidated':
		lines += ['', 'Round cancelled: the match or roster changed. Start a new round with redo:true.']
	elif not closed:
		lines += ['', f"Closes <t:{state['closes_at']}:R> · One successful choice per player."]
	lines += ['', 'Voluntary picks. Select your chosen civ (or Random) in the game.']
	embed = nextcord.Embed(
		title=f"Civ picks · Match #{match_id}" + (' · Final' if state['status'] == 'closed' else ''),
		description='\n'.join(lines), color=0x57F287 if closed else 0x5865F2)
	# Render-only persistent components: the global router owns callbacks.
	# Do not register a View per historical message or auto-ack its presses.
	view = nextcord.ui.View(timeout=None, auto_defer=False, prevent_update=False)
	for i, civ in enumerate([*state['options'], '🎲 Random']):
		view.add_item(nextcord.ui.Button(
			label=f'{i + 1} · {civ}', row=i // 5,
			custom_id=f"civpick:{match_id}:{state['generation']}:{i}",
			disabled=closed or (i != RANDOM and i in state['picks'].values()),
			style=nextcord.ButtonStyle.secondary))
	return embed, view
