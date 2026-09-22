"""Weekly work on the existing betting tick. No per-tick database polling."""
from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.database import db

from . import gold
from .tax_policy import WEEK, first_cutoff, latest_cutoff


class TaxScheduler:
	def __init__(self):
		self.next_run = 0
		self.failures = 0

	async def run_due(self, now):
		if now < self.next_run:
			return
		try:
			policies = await db.fetchall("SELECT * FROM gold_tax_policy WHERE enabled=1") or []
			deadlines = []
			failed = False
			for policy in policies:
				minute = int(policy["minute_of_day"])
				first = first_cutoff(int(policy["activated_at"]), minute)
				cutoff = latest_cutoff(now, minute)
				deadlines.append(max(first, cutoff + WEEK))
				if cutoff < first:
					continue
				try:
					result = await gold.apply_weekly_tax(policy["community_id"], cutoff, now)
					if result is not None:
						log.info(f"Weekly gold tax {policy['community_id']}/{cutoff}: "
							f"{result['taxed_holders']} holders, {result['total_tax']} gold.")
				except Exception as error:
					failed = True
					log.error(f"Weekly gold tax failed for {policy['community_id']}: {error}")
			self.failures = self.failures + 1 if failed else 0
			self.next_run = min(deadlines) if deadlines else float("inf")
			if failed:
				self.next_run = min(self.next_run, now + self._retry_delay())
		except Exception as error:
			self.failures += 1
			self.next_run = now + self._retry_delay()
			log.error(f"Weekly gold tax scheduler failed: {error}")

	def _retry_delay(self):
		return min(86400, 300 * 2 ** min(self.failures - 1, 9))
