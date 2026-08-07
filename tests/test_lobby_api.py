# -*- coding: utf-8 -*-
"""Pure unit tests for nammaoe2bot/features/lobby/api.py parsers (no network). Shapes mirror the
live /api/matches/{id} response verified in the Phase 3 understand workflow:
ISO started/finished strings, per-player `won` bool, per-team teamId, civName."""
import asyncio

from nammaoe2bot.features.lobby import api


def test_parse_iso_epoch_and_invalid():
	assert api.parse_iso("1970-01-01T00:00:00.000Z") == 0
	assert api.parse_iso("2026-06-09T23:00:00.000Z") == api.parse_iso("2026-06-09T23:00:00+00:00")
	assert api.parse_iso(None) is None
	assert api.parse_iso("not-a-date") is None


def test_is_finished():
	assert api.is_finished({"finished": "2026-06-09T23:28:58.000Z"}) is True
	assert api.is_finished({"finished": None}) is False
	assert api.is_finished({}) is False


def test_match_duration_seconds():
	m = {"started": "2026-06-09T23:00:00.000Z", "finished": "2026-06-09T23:28:00.000Z"}
	assert api.match_duration_seconds(m) == 28 * 60
	assert api.match_duration_seconds({"started": "2026-06-09T23:00:00.000Z"}) is None
	assert api.match_duration_seconds({}) is None


def _teams_clean():
	return [
		{"teamId": 1, "players": [{"profileId": 10, "won": True, "civName": "Mongols"},
								  {"profileId": 11, "won": True, "civName": "Franks"}]},
		{"teamId": 2, "players": [{"profileId": 20, "won": False, "civName": "Aztecs"},
								  {"profileId": 21, "won": False, "civName": "Mayans"}]},
	]


def test_winning_teamid_clean():
	assert api.winning_teamid({"teams": _teams_clean()}) == 1


def test_winning_teamid_ambiguous_mixed():
	teams = [
		{"teamId": 1, "players": [{"won": True}, {"won": False}]},
		{"teamId": 2, "players": [{"won": False}, {"won": False}]},
	]
	assert api.winning_teamid({"teams": teams}) is None


def test_winning_teamid_all_none_is_draw():
	teams = [{"teamId": 1, "players": [{"won": None}]}, {"teamId": 2, "players": [{"won": None}]}]
	assert api.winning_teamid({"teams": teams}) is None


def test_winning_teamid_empty():
	assert api.winning_teamid({"teams": []}) is None
	assert api.winning_teamid({}) is None


def test_players_by_team_and_pid_civ_map():
	m = {"teams": _teams_clean()}
	assert api.players_by_team(m) == {1: [10, 11], 2: [20, 21]}
	assert api.pid_civ_map(m) == {10: "Mongols", 11: "Franks", 20: "Aztecs", 21: "Mayans"}


# ── /profiles/{id} — the /link validation source ─────────────────────────
# Bodies below are the live 2026-07-30 responses, verbatim (trimmed of fields
# nothing reads). `name` is the REAL in-game name — the value stored as
# identities.aoe2_name. The 404 body is the trap: it carries a `profileId` of
# its own, so "has a profileId" is NOT enough to call a payload a profile.

LIVE_200_BODY = {
	"avatarhash": "e2b0c1f0",
	"name": "ddk220",
	"profileId": 612690,
	"country": "us",
	"games": "2647",
	"leaderboards": [{"leaderboardId": "rm_team", "rating": 1559}],
}
LIVE_404_BODY = {"success": False, "error": "profile couldn't be found", "profileId": 999999999}


def test_parse_profile_extracts_id_and_name():
	assert api._parse_profile(LIVE_200_BODY) == {"profile_id": 612690, "name": "ddk220"}


def test_parse_profile_returns_none_for_the_404_body():
	assert api._parse_profile(LIVE_404_BODY) is None


def test_parse_profile_coerces_a_string_profile_id():
	"""The same API serialises `games` as a string, so don't trust profileId to
	always arrive as an int — a str id must still parse, as an int."""
	parsed = api._parse_profile({"profileId": "612690", "name": "ddk220"})
	assert parsed == {"profile_id": 612690, "name": "ddk220"}
	assert isinstance(parsed["profile_id"], int)


def test_parse_profile_tolerates_a_missing_name():
	"""A profile with no name is still a real profile — the caller just has no
	in-game name to echo back for the player to eyeball."""
	assert api._parse_profile({"profileId": 612690}) == {"profile_id": 612690, "name": ""}


def test_parse_profile_returns_none_without_a_usable_profile_id():
	assert api._parse_profile({"name": "ddk220"}) is None
	assert api._parse_profile({"profileId": "not-a-number", "name": "ddk220"}) is None
	assert api._parse_profile(None) is None
	assert api._parse_profile("profile couldn't be found") is None


# ── _classify — the whole /link validation decision ──────────────────────
# This mapping is the gate: it decides whether a player is told "your number is
# wrong" (their problem, fixable) or "the service is down" (not their problem,
# retry), and whether anything gets written at all. It is pure so every case can
# be pinned without a network.

def test_classify_reads_a_live_200_profile_body():
	assert api._classify(200, LIVE_200_BODY) == ("ok", {"profile_id": 612690, "name": "ddk220"})


def test_classify_maps_404_to_not_found_not_unavailable():
	"""404 is the ONE status that means "no such profile". Mapped to
	"unavailable" instead, every mistyped id would tell the player the AoE2
	service was broken and leave them retrying a number that will never work —
	and the body it arrives with carries a profileId of its own, so nothing
	downstream would notice."""
	assert api._classify(404, LIVE_404_BODY) == ("not_found", None)


def test_classify_maps_a_server_error_to_unavailable():
	"""A 500 or a 400 says nothing about the id — never blame the number."""
	assert api._classify(500, None) == ("unavailable", None)
	assert api._classify(400, None) == ("unavailable", None)
	assert api._classify(403, None) == ("unavailable", None)


def test_classify_maps_an_unreadable_200_to_unavailable_never_ok():
	"""An unexpected 200 body (an HTML error page, an empty object, an error
	envelope served with a 200) must not become a link — and must not become
	"not_found" either, since it proves nothing about the id."""
	for garbage in (None, "<html>502 Bad Gateway</html>", {}, {"success": False, "error": "nope"}):
		assert api._classify(200, garbage) == ("unavailable", None)


# ── fetch_profile — the I/O wrapper around _classify ─────────────────────
# The mapping being right in a pure helper is worth nothing if the wrapper
# feeds it the wrong thing or swallows a failure as success, so the wrapper is
# driven end-to-end here. conftest.py installs a bare `aiohttp` stub module
# (CI has no aiohttp); the fakes below are monkeypatched onto that same module
# object the lazy `import aiohttp` inside fetch_profile resolves to.

class _FakeClientError(Exception):
	"""Stands in for aiohttp.ClientError — fetch_profile's except clause reads
	that attribute off the module when an exception is raised."""


class _FakeResponse:
	def __init__(self, status, payload=None, error=None):
		self.status = status
		self._payload = payload
		self._error = error

	async def json(self):
		if isinstance(self._payload, Exception):
			raise self._payload
		return self._payload

	async def __aenter__(self):
		if self._error is not None:
			raise self._error
		return self

	async def __aexit__(self, *_a):
		return False


class _FakeSession:
	def __init__(self, response):
		self._response = response
		self.requests = []

	def get(self, url, timeout=None):
		self.requests.append(url)
		return self._response

	async def __aenter__(self):
		return self

	async def __aexit__(self, *_a):
		return False


def _install_fake_aiohttp(monkeypatch, status=200, payload=None, error=None):
	"""Returns the list of URLs the faked session was asked for."""
	import aiohttp

	session = _FakeSession(_FakeResponse(status, payload=payload, error=error))
	monkeypatch.setattr(aiohttp, "ClientError", _FakeClientError, raising=False)
	monkeypatch.setattr(aiohttp, "ClientTimeout", lambda total=None: total, raising=False)
	monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session, raising=False)
	return session.requests


def test_fetch_profile_returns_the_profile_for_a_live_200(monkeypatch):
	requests = _install_fake_aiohttp(monkeypatch, status=200, payload=LIVE_200_BODY)

	assert asyncio.run(api.fetch_profile(612690)) == ("ok", {"profile_id": 612690, "name": "ddk220"})
	assert requests == ["https://data.aoe2companion.com/api/profiles/612690"]


def test_fetch_profile_reports_not_found_for_a_404(monkeypatch):
	"""And on the STATUS alone — the 404 body is never even decoded, so a 404
	served as HTML still reads as "that id does not exist"."""
	_install_fake_aiohttp(monkeypatch, status=404, payload=LIVE_404_BODY)

	assert asyncio.run(api.fetch_profile(999999999)) == ("not_found", None)


def test_fetch_profile_reports_unavailable_for_a_server_error(monkeypatch):
	_install_fake_aiohttp(monkeypatch, status=500, payload=None)

	assert asyncio.run(api.fetch_profile(612690)) == ("unavailable", None)


def test_fetch_profile_reports_unavailable_for_an_unreadable_200(monkeypatch):
	_install_fake_aiohttp(monkeypatch, status=200, payload={"unexpected": "shape"})

	assert asyncio.run(api.fetch_profile(612690)) == ("unavailable", None)


def test_fetch_profile_reports_unavailable_when_the_request_fails(monkeypatch):
	"""The except clause must NEVER produce an "ok". An "ok" here would hand
	/link a profile nothing validated and write the binding anyway — the exact
	failure the whole validation path exists to prevent — and it would do it on
	every outage, for every id, including typos."""
	for boom in (_FakeClientError("connection reset"), TimeoutError(), ValueError("bad json")):
		_install_fake_aiohttp(monkeypatch, status=200, error=boom)
		assert asyncio.run(api.fetch_profile(612690)) == ("unavailable", None)


def test_fetch_profile_reports_unavailable_when_the_body_is_not_json(monkeypatch):
	"""aiohttp raises out of resp.json() (ValueError / ContentTypeError), i.e.
	after the status check — that path must land on "unavailable" too."""
	_install_fake_aiohttp(monkeypatch, status=200, payload=ValueError("Expecting value"))

	assert asyncio.run(api.fetch_profile(612690)) == ("unavailable", None)
