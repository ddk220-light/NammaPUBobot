"""Unit tests for the /changelog feed.

The history itself is baked into data/changelog.json at image build time
(scripts/gen_changelog.py), so what's worth locking down here is that the bot
reads it defensively — a missing or malformed file must degrade to an empty
feed rather than take the command down.
"""
from __future__ import annotations

import json

from bot import changelog


class TestFormatLines:
	def test_renders_date_and_subject(self):
		lines = changelog.format_lines([{"sha": "abc1234", "subject": "Add a thing", "date": "2026-07-28"}])
		assert lines == ["`2026-07-28`  Add a thing"]

	def test_empty_history_says_so(self):
		assert "No changelog" in changelog.format_lines([])[0]

	def test_missing_fields_do_not_raise(self):
		assert changelog.format_lines([{}]) == ["`?`  ?"]


class TestLoad:
	def _write(self, tmp_path, payload):
		path = tmp_path / "changelog.json"
		path.write_text(json.dumps(payload), encoding="utf-8")
		return str(path)

	def test_reads_entries(self, tmp_path):
		path = self._write(tmp_path, {"generated_at": 1, "entries": [
			{"sha": "a", "subject": "one", "date": "2026-01-01"},
			{"sha": "b", "subject": "two", "date": "2026-01-02"},
		]})
		assert len(changelog.load(path)) == 2
		assert changelog.generated_at() == 1

	def test_missing_file_is_empty_not_fatal(self, tmp_path):
		assert changelog.load(str(tmp_path / "nope.json")) == []

	def test_malformed_json_is_empty_not_fatal(self, tmp_path):
		path = tmp_path / "bad.json"
		path.write_text("{not json", encoding="utf-8")
		assert changelog.load(str(path)) == []

	def test_latest_caps_and_orders(self, tmp_path):
		path = self._write(tmp_path, {"entries": [
			{"sha": str(i), "subject": f"s{i}", "date": "2026-01-01"} for i in range(10)]})
		changelog.load(path)
		got = changelog.latest(3)
		assert [e["sha"] for e in got] == ["0", "1", "2"]

	def test_latest_handles_zero_and_negative(self, tmp_path):
		path = self._write(tmp_path, {"entries": [{"sha": "a", "subject": "s", "date": "d"}]})
		changelog.load(path)
		assert changelog.latest(0) == []
		assert changelog.latest(-5) == []


def test_shipped_changelog_is_readable():
	"""The committed fallback must parse — it's what ships if the build step is skipped."""
	entries = changelog.load()
	assert isinstance(entries, list)
	for e in entries:
		assert {"sha", "subject", "date"} <= set(e)
