"""Pure rules for the voluntary civ picker. No game/Discord/network access."""
import random
from datetime import datetime

# Standard AoE2 DE multiplayer catalog, including The Viking Sagas.
# https://www.ageofempires.com/news/faq-the-viking-sagas/
# Deliberately excludes Chronicles and Return of Rome civilizations.
DLC_CIVS = ('Danes', 'Saxons', 'Varangians')
# One fixed calendar month in the community's time zone; restarts never extend it.
DLC_TRIAL_START = int(datetime.fromisoformat('2026-09-23T00:00:00+05:30').timestamp())
DLC_TRIAL_END = int(datetime.fromisoformat('2026-10-23T00:00:00+05:30').timestamp())
CIVS = tuple(sorted("""Armenians,Aztecs,Bengalis,Berbers,Bohemians,Britons,Bulgarians,
Burgundians,Burmese,Byzantines,Celts,Chinese,Cumans,Dravidians,Ethiopians,Franks,
Georgians,Goths,Gurjaras,Hindustanis,Huns,Incas,Italians,Japanese,Jurchens,Khitan,
Khmer,Koreans,Lithuanians,Magyars,Malay,Malians,Mapuche,Mayans,Mongols,Muisca,
Persians,Poles,Portuguese,Romans,Saracens,Shu,Sicilians,Slavs,Spanish,Tatars,
Teutons,Turks,Tupi,Vietnamese,Vikings,Wei,Wu""".replace("\n", "").split(",") + list(DLC_CIVS)))
# Persisted button/choice ID: keep Random at 12 even when bonus civs are offered.
RANDOM = 12


def civ_key(name):
	key = name.strip().casefold()
	return {'inca': 'incas', 'maya': 'mayans', 'khitans': 'khitan', 'khmers': 'khmer'}.get(key, key)


def bonus_civs(now):
	return DLC_CIVS if DLC_TRIAL_START <= now < DLC_TRIAL_END else ()


def select_pool(history, now, rng=None):
	"""Fill only the shortage with repeats; shuffle ties and the displayed order."""
	rng = rng or random
	recent = {}
	for row in history:
		key = civ_key(row['civ'])
		uses, last_at = recent.get(key, (0, 0))
		recent[key] = (uses + int(row['uses']), max(last_at, int(row['last_at'])))
	bonus = bonus_civs(now)
	candidates = [c for c in CIVS if c not in bonus]
	rng.shuffle(candidates)
	candidates.sort(key=lambda c: recent.get(civ_key(c), (0, 0)))
	pool = candidates[:12]
	rng.shuffle(pool)
	return pool


def new_round(roster, options, user_id, now, minutes, previous=None):
	state = dict(generation=(previous or {}).get('generation', 0) + 1,
		roster=roster, options=options, initiator=user_id, opened_at=now,
		closes_at=now + minutes * 60, status='open', picks={}, timed_out=[])
	if bonus := bonus_civs(now):
		# Snapshot offers so open rounds and final cards survive the trial's end.
		state.update(bonus_options=list(bonus), bonus_ends_at=DLC_TRIAL_END)
	return state


def choice_names(state):
	"""Old snapshots lack bonus_options; their choice IDs retain their meaning."""
	return [*state['options'], 'Random', *state.get('bonus_options', [])]


def expire(state, now):
	if state['status'] != 'open' or now < state['closes_at']:
		return False
	for player in state['roster']:
		uid = str(player['id'])
		if uid not in state['picks']:
			state['picks'][uid] = RANDOM
			state['timed_out'].append(uid)
	state['status'] = 'closed'
	return True


def claim(state, user_id, choice, generation, now):
	"""Called only under the session lock. Failed claims do not spend a choice."""
	if generation != state['generation']:
		return 'This round was replaced. Use the latest civ-pick card.'
	expire(state, now)
	if state['status'] != 'open':
		return 'This round is closed.'
	uid = str(user_id)
	if uid not in {str(p['id']) for p in state['roster']}:
		return 'Only players in this match can pick.'
	if uid in state['picks']:
		return 'You have already picked. Each player gets one successful choice.'
	if not 0 <= choice < len(choice_names(state)):
		return 'Invalid civilization option.'
	if choice != RANDOM and choice in state['picks'].values():
		return 'That civilization was just taken. You can still choose another.'
	state['picks'][uid] = choice
	if len(state['picks']) == len(state['roster']):
		state['status'] = 'closed'
	return None


def roster_for(match):
	return [dict(id=p.id, team=i) for i, team in enumerate(match.teams[:2]) for p in team]


def validate_match(match):
	if match is None or not match.ranked:
		return 'Choose a current ranked bot match in this channel.'
	if len(match.teams) < 2 or not all(match.teams[:2]) or any(match.teams[2:]):
		return 'Both teams must be formed before starting a civ pick.'
	roster = roster_for(match)
	ids = [p['id'] for p in roster]
	if len(ids) != len(set(ids)) or set(ids) != {p.id for p in match.players} or len(ids) > 8:
		return 'Wait until teams are fully formed (up to eight players).'
	return None
