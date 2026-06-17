# -*- coding: utf-8 -*-
"""Pure helpers for the quiz feature — custom_id routing, grading, week bucketing,
leaderboard tally and schedule predicates. No DB, no nextcord; everything here is
unit-tested (tests/test_quiz_scoring.py)."""
from __future__ import annotations

import datetime


def parse_custom_id(cid):
	"""Route a component custom_id. Returns (kind, post_id, choice) or None.

	'quiz:{post_id}:reveal'      -> ('reveal', post_id, None)
	'quiz:{post_id}:ans:{index}' -> ('answer', post_id, index)
	Anything else (foreign prefix / non-int parts) -> None.
	"""
	if not cid or not cid.startswith("quiz:"):
		return None
	parts = cid.split(":")
	try:
		if len(parts) == 3 and parts[2] == "reveal":
			return ("reveal", int(parts[1]), None)
		if len(parts) == 4 and parts[2] == "ans":
			return ("answer", int(parts[1]), int(parts[3]))
	except ValueError:
		return None
	return None


def grade(choice_index, correct_index):
	"""True iff the chosen option is the correct one."""
	return int(choice_index) == int(correct_index)


def iso_week_key(ts):
	"""ISO (year, week) bucket for a unix timestamp, UTC."""
	d = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc)
	iso = d.isocalendar()
	return (iso[0], iso[1])


def tally(rows):
	"""Aggregate answer rows into a leaderboard, sorted by correct desc, then
	answered asc, then user_id. Each row needs user_id, nick, is_correct."""
	acc = {}
	order = []
	for r in rows:
		uid = r["user_id"]
		if uid not in acc:
			acc[uid] = {"user_id": uid, "nick": r.get("nick"), "correct": 0, "answered": 0}
			order.append(uid)
		acc[uid]["answered"] += 1
		if int(r.get("is_correct") or 0):
			acc[uid]["correct"] += 1
	out = [acc[u] for u in order]
	out.sort(key=lambda e: (-e["correct"], e["answered"], e["user_id"]))
	return out


def _ymd(ts):
	d = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc)
	return d.strftime("%Y-%m-%d")


def daily_due(now_ts, hour, last_post_ymd):
	"""True iff the current UTC hour >= configured hour and we have not yet posted
	today (last_post_ymd != today). Caller persists today's date after posting.
	Hour is UTC — the bot process runs in UTC on Railway."""
	d = datetime.datetime.fromtimestamp(int(now_ts), datetime.timezone.utc)
	if d.hour < int(hour):
		return False
	return _ymd(now_ts) != (last_post_ymd or "")


def leaderboard_due(now_ts, dow, hour, last_leaderboard_ymd):
	"""True iff today is the configured ISO weekday (1=Mon..7=Sun), the UTC hour is
	reached, and the leaderboard was not already posted today."""
	d = datetime.datetime.fromtimestamp(int(now_ts), datetime.timezone.utc)
	if d.isoweekday() != int(dow) or d.hour < int(hour):
		return False
	return _ymd(now_ts) != (last_leaderboard_ymd or "")
