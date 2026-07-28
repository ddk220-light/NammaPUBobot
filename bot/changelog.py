# -*- coding: utf-8 -*-
"""Recent-changes feed backed by data/changelog.json.

The JSON is baked from git history at image build time (scripts/gen_changelog.py,
wired into the Dockerfile) because the runtime image has no git binary. Loaded
once at import like bot/civ_stats.py — it cannot change while the process runs,
since a new deploy means a new container.

Import-light on purpose (no nextcord, no bot package) so the formatting is
unit-testable.
"""
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
					 "data", "changelog.json")

_entries = []
_generated_at = None


def load(path=_PATH):
	"""Read the baked changelog. Returns the entry list; never raises."""
	global _entries, _generated_at
	try:
		with open(path, encoding="utf-8") as fh:
			data = json.load(fh)
		_entries = data.get("entries") or []
		_generated_at = data.get("generated_at")
	except (OSError, ValueError):
		_entries, _generated_at = [], None
	return _entries


def latest(count=5):
	"""The newest `count` entries, newest first."""
	return _entries[:max(0, int(count))]


def generated_at():
	return _generated_at


def format_lines(entries):
	"""[{sha, subject, date}] -> display lines. Pure."""
	if not entries:
		return ["No changelog available for this build."]
	return [f"`{e.get('date', '?')}`  {e.get('subject', '?')}" for e in entries]


load()
