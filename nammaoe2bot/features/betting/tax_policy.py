"""Weekly tax rules and read-only queries; no application imports or database connection."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WEEK = 7 * 86400
ZONE = ZoneInfo("Asia/Kolkata")
TAX_BALANCE_FLOOR = 500


def latest_cutoff(now, minute_of_day):
	if not 0 <= minute_of_day < 1440:
		raise ValueError("tax time must be a minute of the day")
	local = datetime.fromtimestamp(now, ZONE)
	cutoff = (local - timedelta(days=(local.weekday() - 3) % 7)).replace(
		hour=minute_of_day // 60, minute=minute_of_day % 60, second=0, microsecond=0)
	if cutoff.timestamp() > now:
		cutoff -= timedelta(days=7)
	return int(cutoff.timestamp())


def first_cutoff(activated_at, minute_of_day):
	earliest = activated_at + WEEK
	cutoff = latest_cutoff(earliest, minute_of_day)
	return cutoff if cutoff == earliest else cutoff + WEEK


def tax_amount(balance, seeded_at, cutoff, participated):
	if balance <= TAX_BALANCE_FLOOR or participated or seeded_at is None or seeded_at > cutoff - WEEK:
		return 0
	return min(balance // 10, balance - TAX_BALANCE_FLOOR)


async def tax_candidates(tx, community_id, cutoff, wallets):
	"""Shared by assessment and read-only operational preview; no gold moves."""
	start = cutoff - WEEK
	opportunity = await tx.fetchone(
		"SELECT p.id FROM prediction_posts p "
		"JOIN community_channels c ON c.channel_id=p.channel_id "
		"WHERE c.community_id=%s AND p.opened_at<%s "
		"AND (p.status='open' OR p.freezes_at>%s) "
		"AND (p.status='open' OR p.freezes_at>p.opened_at) LIMIT 1",
		[community_id, cutoff, start])
	if not opportunity:
		return []
	participants = await tx.fetchall(
		"SELECT DISTINCT b.user_id FROM prediction_bets b "
		"JOIN prediction_posts p ON p.id=b.post_id "
		"JOIN community_channels c ON c.channel_id=p.channel_id "
		"WHERE c.community_id=%s AND p.status<>'open' "
		"AND p.freezes_at>=%s AND p.freezes_at<%s AND b.stake>0",
		[community_id, start, cutoff]) or []
	active = {int(r["user_id"]) for r in participants}
	seeds = await tx.fetchall(
		"SELECT user_id, MIN(created_at) AS seeded_at FROM gold_ledger "
		"WHERE community_id=%s AND entry_type='seed' GROUP BY user_id", [community_id]) or []
	seeded = {int(r["user_id"]): int(r["seeded_at"]) for r in seeds}
	owed = []
	for wallet in wallets:
		uid = int(wallet["user_id"])
		amount = tax_amount(int(wallet["balance"]), seeded.get(uid), cutoff, uid in active)
		if amount:
			owed.append((uid, amount))
	return owed
