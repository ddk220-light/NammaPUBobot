"""Unit tests for the user_id-keyed profile-map loader in bot/civ_matcher.

The civ recorder used to map players to AoE2 profiles by **nick**, so a player
renaming silently broke recording — which is why qc_match_civs only ever held a
fraction of matches. ``_load_profile_uid_map`` keys on the stable Discord
``user_id`` instead. These tests lock that behavior down.
"""
from __future__ import annotations

import bot.civ_matcher as cm


def _write_map(tmp_path, *rows):
	p = tmp_path / "player_profile_map.csv"
	p.write_text("user_id,nick,aoe2_name,profile_id,country\n" + "".join(r + "\n" for r in rows), encoding="utf-8")
	return str(p)


def test_keys_on_user_id(tmp_path, monkeypatch):
	monkeypatch.setattr(cm, "_PROFILE_MAP_PATH", _write_map(tmp_path, "111,ddk,ddk220,612690,us"))
	assert cm._load_profile_uid_map() == {111: [612690]}


def test_splits_alt_account_pids(tmp_path, monkeypatch):
	# "17841676 / 2885693" -> two profile ids for one Discord user
	monkeypatch.setattr(cm, "_PROFILE_MAP_PATH", _write_map(tmp_path, "222,thelivi,Mr X,17841676 / 2885693,in"))
	assert cm._load_profile_uid_map() == {222: [17841676, 2885693]}


def test_skips_rows_without_user_id_or_pid(tmp_path, monkeypatch):
	monkeypatch.setattr(cm, "_PROFILE_MAP_PATH", _write_map(
		tmp_path,
		"333,mapped,Mapped,999,in",
		",nouser,NoUser,1000,in",   # blank user_id -> skipped
		"444,nopid,NoPid,,in",      # blank profile_id -> skipped
	))
	assert cm._load_profile_uid_map() == {333: [999]}


def test_missing_file_returns_empty(tmp_path, monkeypatch):
	monkeypatch.setattr(cm, "_PROFILE_MAP_PATH", str(tmp_path / "does_not_exist.csv"))
	assert cm._load_profile_uid_map() == {}
