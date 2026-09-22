"""Preview or explicitly enable/disable a community's weekly tax.

Run inside the deployed service: python -m scripts.gold_tax --community ID
Preview is read-only. --enable preserves an existing active policy; a new or
re-enabled policy gets seven full days of grace. Restart the bot after enabling
so its cached scheduler loads the policy. No schema changes or gold movements.
"""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import runpy
import time

from nammaoe2bot.runtime.paths import REPO_ROOT
from scripts.purge_paused_replay_detail import connection_settings

# Loading this dependency-free file directly avoids betting/__init__.py's
# application bootstrap and schema side effects in an operational CLI.
_rules = runpy.run_path(str(Path(REPO_ROOT) / 'nammaoe2bot/features/betting/tax_policy.py'))


class ReadCursor:
	def __init__(self, cursor):
		self.cursor = cursor

	async def fetchone(self, sql, args=None):
		self.cursor.execute(sql, args)
		return self.cursor.fetchone()

	async def fetchall(self, sql, args=None):
		self.cursor.execute(sql, args)
		return self.cursor.fetchall()


def main():
	import pymysql

	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('--community', type=int, required=True)
	action = parser.add_mutually_exclusive_group()
	action.add_argument('--enable', action='store_true')
	action.add_argument('--disable', action='store_true')
	args = parser.parse_args()
	settings = connection_settings()
	if not all(settings.get(k) for k in ('host', 'user', 'database')):
		parser.error('database environment is not configured')
	conn = pymysql.connect(**settings, cursorclass=pymysql.cursors.DictCursor,
		connect_timeout=15, read_timeout=30, autocommit=False)
	try:
		with conn.cursor() as cur:
			cur.execute('SET TRANSACTION READ ONLY')
			conn.begin()
			cur.execute('SELECT community_id,name FROM communities WHERE community_id=%s', [args.community])
			community = cur.fetchone()
			if not community:
				parser.error('community does not exist')
			cur.execute('SELECT * FROM gold_tax_policy WHERE community_id=%s', [args.community])
			policy = cur.fetchone()
			cur.execute('SELECT quiz_hour FROM quiz_settings WHERE community_id=%s AND enabled=1', [args.community])
			quiz = cur.fetchall()
			if len(quiz) > 1:
				parser.error('multiple enabled quiz schedules; resolve before enabling tax')
			hour = int(quiz[0]['quiz_hour']) if quiz and quiz[0]['quiz_hour'] is not None else 9
			minute = int(policy['minute_of_day']) if policy else (hour * 60 + 330) % 1440
			now = int(time.time())
			cutoff = _rules['latest_cutoff'](now, minute)
			cur.execute('SELECT user_id,balance FROM gold_balances WHERE community_id=%s ORDER BY user_id', [args.community])
			wallets = cur.fetchall()
			owed = asyncio.run(_rules['tax_candidates'](ReadCursor(cur), args.community, cutoff, wallets))
			print(json.dumps(dict(community=community, policy=policy, local_minute_of_day=minute,
				preview_cutoff=cutoff, holders=len(wallets), hypothetical_taxed_holders=len(owed),
				hypothetical_total_tax=sum(v for _, v in owed)), default=str))
			conn.rollback()
			if args.enable or args.disable:
				conn.begin()
				# Existing community row serializes two operators even before a
				# policy exists; never use a missing-row lock as a mutex.
				cur.execute('SELECT community_id FROM communities WHERE community_id=%s FOR UPDATE', [args.community])
				cur.fetchone()
				cur.execute('SELECT * FROM gold_tax_policy WHERE community_id=%s FOR UPDATE', [args.community])
				current = cur.fetchone()
				if args.disable:
					cur.execute('UPDATE gold_tax_policy SET enabled=0 WHERE community_id=%s', [args.community])
				elif not current or not current['enabled']:
					cur.execute('INSERT INTO gold_tax_policy (community_id,enabled,activated_at,minute_of_day) '
						'VALUES (%s,1,%s,%s) ON DUPLICATE KEY UPDATE enabled=1,activated_at=%s,minute_of_day=%s',
						[args.community, now, minute, now, minute])
				cur.execute('SELECT * FROM gold_tax_policy WHERE community_id=%s', [args.community])
				final = cur.fetchone()
				conn.commit()
				print(json.dumps(dict(saved_policy=final)))
				if final and final['enabled']:
					first = _rules['first_cutoff'](int(final['activated_at']), int(final['minute_of_day']))
					print('First eligible Thursday: ' + datetime.fromtimestamp(first, _rules['ZONE']).isoformat())
					print('Restart the bot to load the enabled policy; no gold was deducted by this command.')
	finally:
		conn.rollback()
		conn.close()


if __name__ == '__main__':
	main()
