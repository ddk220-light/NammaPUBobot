"""One public card, numbered buttons, and the final team-by-team summary."""
import nextcord
from .picking import RANDOM, choice_names


def card(match_id, state):
	closed = state['status'] != 'open'
	reveal = state['status'] == 'closed'
	names = choice_names(state)
	lines = []
	for i, civ in enumerate(names):
		if i == RANDOM:
			lines.append('**13. 🎲 Random** — everyone may choose this')
			continue
		if i == RANDOM + 1:
			lines += ['', '**The Viking Sagas · bonus DLC choices**',
				f"Extra choices for rounds opened before <t:{state['bonus_ends_at']}:f>.",
				'Choose only if you own the DLC. One player per civ; available again next match.']
		owner = next((uid for uid, choice in state['picks'].items() if choice == i), None)
		lines.append(f'**{i + 1}. {civ}**' + (f' — <@{owner}>' if reveal and owner else ''))
	for team in (0, 1):
		lines += ['', f'**Team {team + 1}**']
		for player in state['roster']:
			if player['team'] != team:
				continue
			uid = str(player['id'])
			choice = state['picks'].get(uid)
			label = 'Waiting to pick' if choice is None else (
				names[choice] if reveal else 'Picked ✅')
			if reveal and uid in state['timed_out']:
				label += ' (timed out)'
			lines.append(f'<@{uid}> → {label}')
	if state['status'] == 'invalidated':
		lines += ['', 'Round cancelled: the match or roster changed. Start a new round with redo:true.']
	elif not closed:
		lines += ['', f"Closes <t:{state['closes_at']}:R> · One successful choice per player."]
		lines += ['Choices stay hidden until closing. A taken option is rejected privately; try another.']
	lines += ['', 'Voluntary picks. Select your chosen civ (or Random) in the game.']
	embed = nextcord.Embed(
		title=f"Civ picks · Match #{match_id}" + (' · Final' if state['status'] == 'closed' else ''),
		description='\n'.join(lines), color=0x57F287 if closed else 0x5865F2)
	# Render-only persistent components: the global router owns callbacks.
	# Do not register a View per historical message or auto-ack its presses.
	view = nextcord.ui.View(timeout=None, auto_defer=False, prevent_update=False)
	for i, civ in enumerate(names):
		if i == RANDOM:
			civ = '🎲 Random'
		view.add_item(nextcord.ui.Button(
			label=f'{i + 1} · {civ}', row=3 if i > RANDOM else i // 5,
			custom_id=f"civpick:{match_id}:{state['generation']}:{i}",
			# Hiding owners alone is insufficient: a newly disabled button
			# would expose the civ as that player's status changes to Picked.
			disabled=closed,
			style=nextcord.ButtonStyle.secondary))
	return embed, view
