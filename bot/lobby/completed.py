# -*- coding: utf-8 -*-
"""Phase 3 — linked-lobby result sync (Flows 2 & 3).

Driven entirely from LobbyJobs (off the 1s tick) for in_progress qc_lobbies rows
left by the watcher at game launch. For each finished game >= 15 min:
  - Flow 3: record the full game's civs in qc_match_civs (keyed on bot_match_id,
    idempotent against civ_matcher / civ_reconcile — whoever lands first wins).
  - Flow 2: post a single message and gate a ✅ to the LOSING captain; on ✅ ->
    match.report_loss(ctx, captain, draw_flag=False) -> the existing finish path.

STRICTLY ADDITIVE + ISOLATED. No rating/report code is touched; the existing
create-your-own-lobby + manual /report flow is unchanged. Every public coroutine
is guarded; nothing here raises into the tick or the match flow. < 15 min games
are SILENT (no message). An ignored proposal does nothing (the row eventually
expires; the underlying match times out and collapses to a no-op).

The module top imports are kept light (no nextcord / core.client) so the pure
helpers are unit-testable; Discord/client deps are lazy-imported inside the
impure orchestration only.
"""
import time

from core.console import log
from core.database import db

from . import api, profile_map

POLL_AFTER_SECONDS = 120      # re-poll cadence once past the 15-min floor
GIVEUP_AFTER = 6 * 3600       # never-resolved in_progress/awaiting_confirm -> expired


# ── pure helpers (no nextcord / DB / clock) ──────────────────────────────

def parse_pids(s):
	"""'101,102 ,x,' -> [101, 102] (ints only)."""
	out = []
	for part in (s or "").split(","):
		part = part.strip()
		if part.isdigit():
			out.append(int(part))
	return out


def next_poll_at(now):
	return now + POLL_AFTER_SECONDS


def should_giveup(created_at, now, giveup_after=GIVEUP_AFTER):
	return (now - (created_at or 0)) > giveup_after


def should_record_civs(winner_idx, win_tid):
	"""Record Flow 3 civs only when the W/L is trustworthy: a winner we mapped to a
	bot team (winner_idx set), or a genuine API draw (win_tid None -> result NULL is
	correct). If a REAL winner exists (win_tid set) but we couldn't map it
	(winner_idx None), skip — civ_matcher will backfill the bot's authoritative W/L
	after the report, and the idempotency guard would otherwise lock in our NULL."""
	return winner_idx is not None or win_tid is None


def _which_team(user_ids, bot_teams):
	"""Index (0/1) of the single bot team containing any of user_ids, else None
	(none, or split across both)."""
	hits = [i for i, team in enumerate(bot_teams) if user_ids & {p.id for p in team}]
	return hits[0] if len(hits) == 1 else None


def resolve_result(match_api, profile_to_user, bot_teams):
	"""Return ``(winner_idx, losing_captain)``.

	``winner_idx`` ∈ {0,1,None} is the bot team that won (API-derived; used for
	Flow 3 W/L and the winner-name hint). ``losing_captain`` is the member to gate
	the ✅ to, or None to DEGRADE to a generic prompt. Pure.

	Degrades (captain=None) when: the winner is ambiguous, the winning side does
	not map cleanly to exactly one bot team, or too few losing-team members are
	resolved through the profile map to be confident.
	"""
	win_tid = api.winning_teamid(match_api)
	if win_tid is None:
		return None, None
	by_team = api.players_by_team(match_api)
	win_users = {profile_to_user[p] for p in by_team.get(win_tid, []) if p in profile_to_user}
	win_idx = _which_team(win_users, bot_teams)
	if win_idx is None:
		return None, None

	losing_team = bot_teams[1 - win_idx]
	lose_pids = [p for tid, pids in by_team.items() if tid != win_tid for p in pids]
	lose_users = {profile_to_user[p] for p in lose_pids if p in profile_to_user}
	resolved_in_losing = lose_users & {p.id for p in losing_team}
	captain = losing_team[0] if len(losing_team) else None
	if captain is not None and (captain.id in resolved_in_losing or len(resolved_in_losing) >= 2):
		return win_idx, captain
	return win_idx, None   # winner known, but losing captain not confidently gated -> degrade


# ── impure orchestration (lazy Discord/client imports) ───────────────────

async def resolve_row(row):
	"""Per-row worker dispatched by LobbyJobs (the caller also guards). Resolves
	one in_progress/awaiting_confirm qc_lobbies row to a posted result or a no-op,
	transitioning its status. Self-contained + guarded; never raises upward."""
	import bot

	now = int(time.time())
	row_id = row.get("id")
	game_id = row.get("aoe2_game_id")

	# 1) Give up on rows that will never resolve (cheap, no API).
	if should_giveup(row.get("created_at"), now):
		await _set_status(row_id, "expired")
		return

	# 2) Resolve the live bot match FIRST. If it is gone or already past report
	#    (reported manually / cancelled / timed out), do nothing but close the row.
	match = next((m for m in bot.active_matches if m.id == row.get("match_id")), None)
	if match is None or match.state != match.WAITING_REPORT:
		await _set_status(row_id, "completed")   # resolved out-of-band: no message, no ratings
		return

	# 3) Already posted (awaiting_confirm): only watch for confirm / give-up — no
	#    redundant API calls. The confirm transition is the match-gone branch above.
	if row.get("completed_message_id"):
		await _reschedule(row_id, now)
		return

	# 4) Fetch the finished match by id.
	match_api = await api.fetch_match_by_id(game_id)
	if match_api is None or not api.is_finished(match_api):
		await _reschedule(row_id, now)
		return

	# 5) Duration gate — < 15 min is SILENT (still record civs, then close).
	dur = api.match_duration_seconds(match_api)
	if dur is None or dur < api.MIN_DURATION_SECONDS:
		await _safe_record_civs(match, match_api, now)
		await _set_status(row_id, "completed")
		return

	# 6) Flow 3 (record civs) then Flow 2 (post + gate the losing captain's ✅).
	profile_to_user = await _profile_to_user(parse_pids(row.get("profile_ids")))
	win_tid = api.winning_teamid(match_api)
	winner_idx, losing_captain = resolve_result(
		match_api, profile_to_user, [match.teams[0], match.teams[1]]
	)
	# Roster-divergence guard: if /subauto (or any roster change) happened after
	# the lobby was captured, the frozen profileIds may map to players no longer in
	# this match — don't trust the team mapping for the winner hint / W/L.
	if not set(profile_to_user.values()).issubset({p.id for p in match.players}):
		winner_idx, losing_captain = None, None
	# Only record civs when W/L is trustworthy (a mapped winner or a genuine API
	# draw); if a real winner exists but we couldn't map it, skip so civ_matcher
	# backfills the bot's authoritative W/L instead of us writing result=NULL.
	if should_record_civs(winner_idx, win_tid):
		await _safe_record_civs(match, match_api, now, winner_idx=winner_idx)
	await _post_result_and_gate(match, row, winner_idx, losing_captain)


async def _post_result_and_gate(match, row, winner_idx, losing_captain):
	"""Post the result message, add ✅, register the loss-confirm handler, and move
	the row to awaiting_confirm. Best-effort; a failure leaves the row in_progress
	to be retried (or it expires)."""
	import bot
	from nextcord import DiscordException

	row_id = row.get("id")
	now = int(time.time())
	# Claim the row first: bump next-poll so a partial failure below can't re-fire
	# the post every 15s poll pass (it retries on the normal 120s cadence instead).
	await _reschedule(row_id, now)

	try:
		ctx = bot.SystemContext(match.queue.qc)
	except Exception as e:
		log.error(f"Flow2 ctx build failed (match {match.id}): {e}")
		return

	if losing_captain is not None and winner_idx is not None:
		text = (
			f"🏆 Game over — **{match.teams[winner_idx].name}** won (match #{match.id}).\n"
			f"{losing_captain.mention} (losing captain), react ✅ to report the loss, "
			f"or use `/report loss`."
		)
	else:
		text = (
			f"🏁 Game over (match #{match.id}).\n"
			f"Losing captain: react ✅ to report the loss, or use `/report loss`."
		)

	try:
		message = await ctx.channel.send(text)
	except DiscordException as e:
		log.warning(f"Flow2 send failed (match {match.id}): {e}")
		return

	# Durably mark posted BEFORE wiring the reaction, so a crash here can't
	# double-post (a re-picked row short-circuits on completed_message_id). If the
	# write fails, delete the orphan message and leave the row to retry cleanly.
	try:
		await db.update(
			"qc_lobbies",
			{"status": "awaiting_confirm", "completed_message_id": message.id,
			 "last_edit_at": next_poll_at(now)},
			keys={"id": row_id},
		)
	except Exception as e:
		log.error(f"Flow2 status update failed (match {match.id}): {e}")
		try:
			await message.delete()
		except DiscordException:
			pass
		return

	bot.waiting_reactions[message.id] = LossConfirm(match, message, losing_captain).process_reaction
	try:
		await message.add_reaction("✅")
	except DiscordException:
		pass
	log.info(f"Flow2: posted loss-confirm for match {match.id} (game {row.get('aoe2_game_id')}).")


class LossConfirm:
	"""✅-reaction handler registered in bot.waiting_reactions. Mirrors the
	check-in reaction idiom: it relies on match.report_loss self-gating to
	captains (a non-captain ✅ raises PermissionError and is swallowed), so the
	right captain's click reports their team's loss while everyone else is inert."""

	def __init__(self, match, message, losing_captain=None):
		self.m = match
		self.message = message
		self.losing_captain = losing_captain

	async def process_reaction(self, reaction, user, remove=False):
		import bot
		from core.client import dc

		if remove:
			return
		if str(reaction) != "✅" or user.id == dc.user.id:
			return
		# When we confidently identified the losing captain, ONLY they may confirm —
		# otherwise the winning captain's one click would invert the ranked result
		# (report_loss reports the reactor's own team as the loser). Stay live for
		# the right captain. In the degraded path (no captain known) we fall back to
		# report_loss's own captain self-gating, same trust as manual /report loss.
		if self.losing_captain is not None and user.id != self.losing_captain.id:
			return
		if self.m.state != self.m.WAITING_REPORT:
			bot.waiting_reactions.pop(self.message.id, None)   # stale -> unsubscribe
			return
		try:
			ctx = bot.SystemContext(self.m.queue.qc)
			await self.m.report_loss(ctx, user, False)
			bot.waiting_reactions.pop(self.message.id, None)   # success -> unsubscribe
		except (bot.Exc.PermissionError, bot.Exc.MatchStateError):
			pass   # wrong reactor / stale state — stay live for the real captain
		except Exception as e:
			log.error(f"LossConfirm report_loss failed (match {self.m.id}): {e}")


async def record_civs_by_id(channel_id, bot_match_id, match_api, players, winner, match_at):
	"""Flow 3 — write qc_match_civs from a KNOWN gameId's match object. Mirrors the
	back half of civ_matcher._find_and_record (idempotency guard + row dict +
	insert_many) but skips the API search (we already have the exact game). The
	guard makes it compose with the existing writers (first one wins)."""
	if await db.fetchone("SELECT 1 AS x FROM qc_match_civs WHERE bot_match_id=%s LIMIT 1", [bot_match_id]):
		return True
	pid_civ = api.pid_civ_map(match_api)
	if not pid_civ:
		return False
	aoe2_match_id = match_api.get("matchId")
	rows = []
	for user_id, nick, team in players:
		pids = await _profiles_for(user_id)
		civ = next((pid_civ[pid] for pid in pids if pid in pid_civ), None)
		if not civ:
			continue
		result = ("W" if team == winner else "L") if (winner is not None and team is not None) else None
		rows.append(dict(
			channel_id=channel_id, aoe2_match_id=aoe2_match_id, aoe2_name="",
			civ=civ, at=match_at, bot_match_id=bot_match_id,
			user_id=user_id, nick=nick, team=team, result=result,
		))
	if not rows:
		return False
	await db.insert_many("qc_match_civs", rows)
	log.info(f"Flow3: recorded {len(rows)} civs for bot match {bot_match_id} (aoe2 {aoe2_match_id}).")
	return True


# ── small impure helpers ─────────────────────────────────────────────────

async def _safe_record_civs(match, match_api, now, winner_idx=None):
	try:
		from core.utils import get_nick
		players = [
			(p.id, get_nick(p), 0 if p in match.teams[0] else (1 if p in match.teams[1] else None))
			for p in match.players
		]
		await record_civs_by_id(match.qc.id, match.id, match_api, players, winner_idx, now)
	except Exception as e:
		log.error(f"Flow3 civ record failed (match {match.id}): {e}")


async def _profile_to_user(profile_ids):
	"""{profileId: user_id} for the captured pids, from qc_profile_map."""
	try:
		return await profile_map.known_for(profile_ids)
	except Exception as e:
		log.error(f"profile_to_user lookup failed: {e}")
		return {}


async def _profiles_for(user_id):
	"""[profileId, ...] for a discord user — qc_profile_map first, CSV fallback."""
	try:
		rows = await db.select(["profile_id"], "qc_profile_map", where={"user_id": user_id}, order_by="linked_at")
		pids = [r["profile_id"] for r in (rows or [])]
		if pids:
			return pids
	except Exception as e:
		log.error(f"profiles_for({user_id}) db lookup failed: {e}")
	try:
		from bot.civ_matcher import _load_profile_uid_map
		return _load_profile_uid_map().get(user_id, [])
	except Exception:
		return []


async def _set_status(row_id, status):
	try:
		await db.update("qc_lobbies", {"status": status, "last_edit_at": int(time.time())}, keys={"id": row_id})
	except Exception as e:
		log.error(f"qc_lobbies status->{status} failed (row {row_id}): {e}")


async def _reschedule(row_id, now):
	try:
		await db.update("qc_lobbies", {"last_edit_at": next_poll_at(now)}, keys={"id": row_id})
	except Exception as e:
		log.error(f"qc_lobbies reschedule failed (row {row_id}): {e}")
