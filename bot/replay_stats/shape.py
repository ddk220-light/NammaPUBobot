# -*- coding: utf-8 -*-
"""Pure transforms from extract_match() output to rs_* MySQL row dicts. Adds aoe2_match_id,
denormalizes profile_id onto the long-form tables (via the per-match player_number->profile_id
map), and attributes Discord user_id from a profile_id->user_id map. No DB — unit-tested."""

REPLAY_URL = "https://www.aoe2insights.com/match/{id}/"

_PLAYER_GAME_FIELDS = (
    "player_number", "profile_id", "identity", "attribution", "civ", "team", "winner",
    "eapm", "age_reliable", "tc_relocations", "feudal_s", "castle_s", "imperial_s",
    "first_tc_s", "villagers", "vil_pre_feudal", "vil_pre_castle", "vil_pre_imperial",
    "military", "mil_pre_feudal", "mil_pre_castle", "mil_pre_imperial",
)
_UNIT_FIELDS = ("player_number", "unit", "category", "is_military",
                "total", "pre_feudal", "pre_castle", "pre_imperial")
_TECH_FIELDS = ("player_number", "tech", "click_s", "phase")
_BUILDING_FIELDS = ("player_number", "building", "count")
_EVENT_FIELDS = ("player_number", "kind", "name", "category", "is_military", "amount", "t_s")


def match_row(m, bot_match_id, parsed_at, parser_version):
    aoe2_id = m["aoe2_match_id"]
    return dict(
        aoe2_match_id=aoe2_id, bot_match_id=bot_match_id, map=m.get("map"),
        save_version=m.get("save_version"), duration_s=m.get("duration_s"),
        played_at=m.get("date") or None, replay_url=REPLAY_URL.format(id=aoe2_id),
        parsed_at=parsed_at, parser_version=parser_version,
    )


def pnum_to_profile(players):
    return {p["player_number"]: p["profile_id"] for p in players}


def player_game_rows(aoe2_match_id, players, profmap):
    out = []
    for p in players:
        row = {k: p.get(k) for k in _PLAYER_GAME_FIELDS}
        row["aoe2_match_id"] = aoe2_match_id
        row["user_id"] = profmap.get(p["profile_id"])
        out.append(row)
    return out


def _long_rows(aoe2_match_id, records, pnum2profile, fields):
    out = []
    for r in records:
        row = {k: r.get(k) for k in fields}
        row["aoe2_match_id"] = aoe2_match_id
        row["profile_id"] = pnum2profile.get(r["player_number"])
        out.append(row)
    return out


def unit_rows(aoe2_match_id, units, pnum2profile):
    return _long_rows(aoe2_match_id, units, pnum2profile, _UNIT_FIELDS)


def tech_rows(aoe2_match_id, techs, pnum2profile):
    return _long_rows(aoe2_match_id, techs, pnum2profile, _TECH_FIELDS)


def building_rows(aoe2_match_id, buildings, pnum2profile):
    return _long_rows(aoe2_match_id, buildings, pnum2profile, _BUILDING_FIELDS)


def event_rows(aoe2_match_id, events, pnum2profile):
    """Per-action production timeline -> rs_player_events rows. Assigns a per-(match,player) seq
    in time order so the composite PK (match, player, seq) is unique and re-ingest-safe (a player
    can queue the same unit many times)."""
    ordered = sorted(events, key=lambda e: (e["player_number"], e.get("t_s") or 0,
                                            e.get("name") or "", e.get("amount") or 0))
    seqs, out = {}, []
    for e in ordered:
        pn = e["player_number"]
        s = seqs.get(pn, 0)
        seqs[pn] = s + 1
        row = {k: e.get(k) for k in _EVENT_FIELDS}
        row["aoe2_match_id"] = aoe2_match_id
        row["profile_id"] = pnum2profile.get(pn)
        row["seq"] = s
        out.append(row)
    return out


def profile_upserts(players, profmap, now):
    return [dict(profile_id=p["profile_id"], user_id=profmap.get(p["profile_id"]),
                 name=p.get("identity"), last_seen_at=now) for p in players]
