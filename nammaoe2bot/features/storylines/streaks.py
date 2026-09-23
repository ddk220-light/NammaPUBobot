"""Current ranked streak leaders, independent of the narrative history window."""

CLIPS = {
	3: 'Killing Spree', 4: 'Dominating', 5: 'Mega Kill', 6: 'Unstoppable',
	7: 'Wicked Sick', 8: 'Monster Kill', 9: 'Godlike', 10: 'Beyond Godlike',
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
		lines.append(f'👑 **{nick[uid]}** leads the match — **{CLIPS[clip_for(wins)]}!**')
	return '\n'.join(lines)
