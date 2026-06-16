"""Unit tests for the alternate-rating snapshot loader/builder.

The /leaderboard_alternate command shows a what-if Elo snapshot — what ratings
would be without the blanket weekly uncertainty (sigma) decay. The snapshot is
precomputed into data/alt_ratings.csv; these tests pin the pure load/merge logic
so the command itself stays a thin renderer.
"""
from __future__ import annotations

import bot.alt_ratings as ar


def _write(tmp_path, *rows, header="user_id,nick,current_rating,alt_rating,alt_deviation,games,branch_date,computed_date"):
	p = tmp_path / "alt_ratings.csv"
	p.write_text(header + "\n" + "".join(r + "\n" for r in rows), encoding="utf-8")
	return str(p)


def test_load_returns_alt_rating_and_deviation_keyed_by_user_id(tmp_path):
	path = _write(tmp_path, "111,ddk,1410,1330,80,307,2025-11-17,2026-06-15")
	assert ar.load_alt_ratings(path) == {111: {"alt_rating": 1330, "alt_deviation": 80}}


def test_load_missing_file_returns_empty(tmp_path):
	assert ar.load_alt_ratings(str(tmp_path / "nope.csv")) == {}


def test_load_skips_rows_with_bad_numbers(tmp_path):
	path = _write(
		tmp_path,
		"111,ddk,1410,1330,80,307,2025-11-17,2026-06-15",
		",nouser,1,2,3,4,x,y",          # blank user_id -> skipped
		"222,bad,1000,notanint,90,5,x,y",  # non-int alt_rating -> skipped
	)
	assert ar.load_alt_ratings(path) == {111: {"alt_rating": 1330, "alt_deviation": 80}}


def test_meta_reads_dates_from_first_row(tmp_path):
	path = _write(tmp_path, "111,ddk,1410,1330,80,307,2025-11-17,2026-06-15")
	assert ar.load_snapshot_meta(path) == {"branch_date": "2025-11-17", "computed_date": "2026-06-15"}


def test_meta_missing_file_returns_empty(tmp_path):
	assert ar.load_snapshot_meta(str(tmp_path / "nope.csv")) == {}


def test_build_sorts_by_alt_descending_with_signed_delta():
	players = [
		{"user_id": 1, "nick": "A", "rating": 1000},
		{"user_id": 2, "nick": "B", "rating": 1500},
		{"user_id": 3, "nick": "C", "rating": 1200},
	]
	alt = {
		1: {"alt_rating": 1300, "alt_deviation": 80},  # +300
		2: {"alt_rating": 1400, "alt_deviation": 80},  # -100
		3: {"alt_rating": 1200, "alt_deviation": 80},  # 0
	}
	rows = ar.build_alt_leaderboard(players, alt)
	assert [r["nick"] for r in rows] == ["B", "A", "C"]  # 1400, 1300, 1200
	assert [r["delta"] for r in rows] == [-100, 300, 0]
	assert rows[0] == {"user_id": 2, "nick": "B", "current": 1500, "alt": 1400, "delta": -100}


def test_build_falls_back_to_current_when_player_absent_from_snapshot():
	players = [{"user_id": 9, "nick": "New", "rating": 1111}]
	rows = ar.build_alt_leaderboard(players, {})
	assert rows == [{"user_id": 9, "nick": "New", "current": 1111, "alt": 1111, "delta": 0}]
