#!/usr/bin/env python3
"""Cross-reference bot matches with AoE2 Companion API matches."""

import json
import urllib.request
from datetime import datetime, timedelta

AOE2_API = "https://data.aoe2companion.com/api"

# Bot nick -> AoE2 profile ID mapping (confirmed)
PROFILE_MAP = {
    "feederTony": 3695055,
    "M1k3": 7992697,
    "Gajini": 2083497,
    "ninjavarier": 2669967,
    "centurion12": 2093241,
    "Shadeslayer": 1590954,  # "Shadeslayer II" in-game
    "ddk": 612690,
    "Kaipullae": 5925393,
    "guruGreatest": 12297184,
    "drgameher": 5859931,
    "newisyou": 572601,
    "wallandboom": 4843062,
    "MuttonKuska": 4984526,
    "bloodless.": 593356,
    "TiShi": 2876149,
    "thelivi": 17841676,  # "Mr.Livi" in-game
    "sundar7238": None,
    "stylishsteam": None,
    "bearknightman": None,
    "tokenbadteam": None,
    "I'm Dead Guyzzz...": None,
    "tyPo": None,
    "tyPo Fan": None,
    "rusher": 18419878,  # "RusheR" (India)
    "fenrir05": None,  # many Fenrirs, unclear which
}

# AoE2 profile ID -> in-game name (additional players seen in matches)
EXTRA_PROFILES = {
    3937482: "mutanT",
    15431072: "vetrierai",
    2838261: "Maximus",
    7499537: "MR.Jithu",
    2704967: "Venom",
    19236842: "Harry",
    3153454: "TRush",
    2441496: "Dark_Knight",
    1314165: "Arkantos12",
    3446474: "Ste@|\\/|",
    9039952: "KiTz",
    6823812: "Aqua Sama",
    4322802: "Blo0D",
}

# Last 10 bot matches with players (from CSV data)
BOT_MATCHES = [
    {
        "match_id": 1350759, "at": "2026-03-01 19:28:38", "winner": 1,
        "players": {
            0: ["Shadeslayer", "tyPo Fan", "guruGreatest", "Kaipullae"],
            1: ["MuttonKuska", "Dark De Bruyne", "ddk", "M1k3"],
        }
    },
    {
        "match_id": 1350746, "at": "2026-03-01 18:56:54", "winner": 0,
        "players": {
            0: ["ddk", "bloodless.", "TiShi"],
            1: ["Shadeslayer", "I'm Dead Guyzzz...", "guruGreatest", "bearknightman"],
        }
    },
    {
        "match_id": 1350729, "at": "2026-03-01 18:26:21", "winner": 1,
        "players": {
            0: ["bloodless.", "guruGreatest", "Kaipullae", "tokenbadteam"],
            1: ["drgameher", "I'm Dead Guyzzz...", "ddk", "M1k3"],
        }
    },
    {
        "match_id": 1350708, "at": "2026-03-01 17:46:40", "winner": 1,
        "players": {
            0: ["rusher", "TiShi", "M1k3"],
            1: ["MuttonKuska", "Shadeslayer", "I'm Dead Guyzzz..."],
        }
    },
    {
        "match_id": 1350606, "at": "2026-03-01 14:12:44", "winner": 0,
        "players": {
            0: ["fenrir05", "I'm Dead Guyzzz...", "sundar7238", "tyPo"],
            1: ["newisyou", "wallandboom", "bloodless.", "stylishsteam"],
        }
    },
    {
        "match_id": 1350595, "at": "2026-03-01 13:24:07", "winner": 0,
        "players": {
            0: ["fenrir05", "I'm Dead Guyzzz...", "wallandboom"],
            1: ["newisyou", "thelivi", "bloodless.", "tyPo"],
        }
    },
    {
        "match_id": 1350585, "at": "2026-03-01 12:49:05", "winner": 1,
        "players": {
            0: ["fenrir05", "wallandboom", "bloodless.", "tyPo"],
            1: ["thelivi", "Gajini", "guruGreatest", "M1k3"],
        }
    },
    {
        "match_id": 1350578, "at": "2026-03-01 12:00:28", "winner": 0,
        "players": {
            0: ["fenrir05", "newisyou", "thelivi", "Kaipullae"],
            1: ["Shadeslayer", "I'm Dead Guyzzz...", "guruGreatest", "tyPo"],
        }
    },
    {
        "match_id": 1350572, "at": "2026-03-01 11:19:40", "winner": 1,
        "players": {
            0: ["MuttonKuska", "drgameher", "Shadeslayer", "thelivi"],
            1: ["wallandboom", "stylishsteam", "sundar7238", "tokenbadteam"],
        }
    },
    {
        "match_id": 1350569, "at": "2026-03-01 10:41:28", "winner": 0,
        "players": {
            0: ["bloodless.", "tyPo", "M1k3", "Kaipullae"],
            1: ["newisyou", "I'm Dead Guyzzz...", "guruGreatest", "TiShi"],
        }
    },
]

# Known AoE2 API matches on March 1, 2026 (from earlier fetches)
API_MATCHES_MAR1 = [
    {"matchId": 460010061, "started": "2026-03-01T18:02:43Z",
     "players": ["Shadeslayer II", "GuruGreatest", "Arkantos12", "Kaipullae",
                  "M1k3 Chan!", "mutton_kuska", "Dark_Knight", "ddk220"]},
    {"matchId": 460002527, "started": "2026-03-01T17:30:06Z",
     "players": ["MR.Jithu", "TiShi", "Kaipullae", "ddk220",
                  "vetrierai", "GuruGreatest", "KIT WALKER", "Shadeslayer II"]},
    {"matchId": 459993088, "started": "2026-03-01T16:55:58Z",
     "players": ["Baby", "GuruGreatest", "Kaipullae", "MR.Jithu",
                  "DrGameHer", "M1k3 Chan!", "ddk220", "vetrierai"]},
    {"matchId": 459980088, "started": "2026-03-01T16:06:10Z",
     "players": ["M1k3 Chan!", "RusheR", "TiShi", "mutton_kuska",
                  "Shadeslayer II", "vetrierai"]},
    {"matchId": 459920352, "started": "2026-03-01T11:17:23Z",
     "players": ["MR.Jithu", "WallAndBoom", "Fenrir", "mutanT",
                  "M1k3 Chan!", "Gajini", "Mr.Livi", "GuruGreatest"]},
    {"matchId": 459918111, "started": "2026-03-01T11:08:31Z",
     "players": ["MR.Jithu", "WallAndBoom", "Fenrir", "mutanT",
                  "GuruGreatest", "Gajini", "Mr.Livi", "M1k3 Chan!"]},
    {"matchId": 459912029, "started": "2026-03-01T10:25:49Z",
     "players": ["newisyou", "Kaipullae", "Fenrir", "Mr.Livi",
                  "vetrierai", "GuruGreatest", "Shadeslayer II", "mutanT"]},
    {"matchId": 459900302, "started": "2026-03-01T09:03:46Z",
     "players": ["MR.Jithu", "mutanT", "Kaipullae", "M1k3 Chan!",
                  "newisyou", "TiShi", "GuruGreatest", "vetrierai"]},
    {"matchId": 459893658, "started": "2026-03-01T08:15:04Z",
     "players": ["Dark_Knight", "Kaipullae", "TiShi", "Arkantos12",
                  "MR.Jithu", "mutanT", "GuruGreatest", "vetrierai"]},
]

# Name normalization map: bot nick -> possible AoE2 in-game names
NAME_MAP = {
    "Shadeslayer": "Shadeslayer II",
    "M1k3": "M1k3 Chan!",
    "MuttonKuska": "mutton_kuska",
    "ddk": "ddk220",
    "guruGreatest": "GuruGreatest",
    "Kaipullae": "Kaipullae",
    "drgameher": "DrGameHer",
    "newisyou": "newisyou",
    "wallandboom": "WallAndBoom",
    "TiShi": "TiShi",
    "thelivi": "Mr.Livi",
    "Gajini": "Gajini",
    "fenrir05": "Fenrir",
    "bloodless.": "Bloodless",
    "rusher": "RusheR",
    "centurion12": "centurion",
    "ninjavarier": "ninjavarier",
}


def match_score(bot_match, api_match):
    """Score how well a bot match maps to an API match based on player overlap."""
    bot_players_all = []
    for team in bot_match["players"].values():
        bot_players_all.extend(team)

    # Normalize bot player names
    api_names = set(p.lower() for p in api_match["players"])
    matched = 0
    total = len(bot_players_all)

    for bp in bot_players_all:
        aoe_name = NAME_MAP.get(bp, bp)
        if aoe_name.lower() in api_names:
            matched += 1

    return matched, total


def cross_reference():
    """Try to match bot matches to API matches."""
    print("Cross-referencing bot matches with AoE2 Companion API matches")
    print("=" * 70)

    for bot_match in BOT_MATCHES:
        bot_time = datetime.strptime(bot_match["at"], "%Y-%m-%d %H:%M:%S")
        bot_players_all = []
        for team in bot_match["players"].values():
            bot_players_all.extend(team)

        print(f"\nBot Match {bot_match['match_id']} at {bot_match['at']}")
        print(f"  Players: {', '.join(bot_players_all)}")

        best_match = None
        best_score = 0
        for api_match in API_MATCHES_MAR1:
            api_time = datetime.fromisoformat(api_match["started"].replace("Z", "+00:00")).replace(tzinfo=None)
            time_diff = abs((bot_time - api_time).total_seconds()) / 60

            if time_diff > 120:  # Skip if more than 2 hours apart
                continue

            matched, total = match_score(bot_match, api_match)
            score = matched / total if total > 0 else 0

            if score > best_score:
                best_score = score
                best_match = (api_match, matched, total, time_diff)

        if best_match:
            api_match, matched, total, time_diff = best_match
            print(f"  -> API Match {api_match['matchId']} (started {api_match['started']})")
            print(f"     Player overlap: {matched}/{total} ({best_score:.0%}), time diff: {time_diff:.0f}min")
            print(f"     API Players: {', '.join(api_match['players'])}")
        else:
            print(f"  -> No matching API match found")


if __name__ == "__main__":
    cross_reference()
