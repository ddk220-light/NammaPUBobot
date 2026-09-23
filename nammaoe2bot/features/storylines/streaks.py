"""Current ranked streak leaders, independent of the narrative history window."""

ANNOUNCEMENTS = {
	3: '{player} is on a **Killing Spree**!',
	4: '{player} is **Dominating**!',
	5: '{player} is on a **Mega Kill** streak!',
	6: '{player} is **Unstoppable**!',
	7: '{player} is **Wicked Sick**!',
	8: '{player} is on a **Monster Kill** streak!',
	9: '{player} is **GODLIKE**!',
	10: '{player} is **beyond GODLIKE**, someone kill them!!',
}


def leaders(ids, streaks):
	values = {uid: max(0, int(streaks.get(uid) or 0)) for uid in ids}
	best = max(values.values(), default=0)
	return best, [uid for uid, value in values.items() if value == best] if best else []


def clip_for(wins):
	return min(wins, 10) if wins >= 3 else None


def match_leader(match):
	"""Only a unique overall leader earns audio, including ties within one team."""
	if not getattr(match, 'ranked', False) or getattr(match, 'streaks', None) is None:
		return None
	wins, ids = leaders([p.id for team in match.teams[:2] for p in team], match.streaks)
	return (ids[0], wins) if len(ids) == 1 and clip_for(wins) else None


def summary(match, nick):
	if not getattr(match, 'ranked', False) or getattr(match, 'streaks', None) is None:
		return ''
	lines = []
	for team in match.teams[:2]:
		wins, ids = leaders([p.id for p in team], match.streaks)
		if wins < 3:
			continue
		names = ', '.join(f'**{nick[uid]}**' for uid in ids)
		text = f'{names} — **{wins}** consecutive wins'
		if len(ids) > 1:
			text += ' (tied)'
		lines.append(f'**{team.name}:** {text}')
	if not lines:
		return ''
	lines.insert(0, '**🔥 Current win streaks**')
	if leader := match_leader(match):
		uid, wins = leader
		lines.append('👑 ' + ANNOUNCEMENTS[clip_for(wins)].format(player=f'**{nick[uid]}**'))
	return '\n'.join(lines)
