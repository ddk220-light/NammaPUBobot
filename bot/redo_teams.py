# -*- coding: utf-8 -*-
"""
Parse PUBobot match messages and run captain-based matchmaking for comparison.
"""

import re
from itertools import combinations
from collections import namedtuple


Player = namedtuple('Player', ['id', 'name'])


def parse_embed_match(embed):
    """Parse a PUBobot embed to extract teams and players.

    Expected embed structure:
    - Field names: "{emoji} ​ **{TeamName}** ​ `〈{avg_rating}〉`"
    - Field values: " ​ `〈Rank〉`<@uid> ​ `〈Rank〉`<@uid> ..."
    - Footer: "Match id: {id}"

    Returns list of team dicts or None if parsing fails.
    """
    teams = []

    for field in embed.fields:
        # Parse team header from field name
        # Format: :emoji: ​ **TeamName** ​ `〈avg〉`
        header = re.search(r'(:\w+:).*?\*\*(\w+)\*\*.*?〈(\d+)〉', field.name or '')
        if not header:
            continue

        team = {
            'emoji': header.group(1),
            'name': header.group(2),
            'avg_rating': int(header.group(3)),
            'players': [],
        }

        # Parse players from field value
        # Format: `〈Rank〉`<@uid> or 〈Rank〉<@uid>
        for rank, uid in re.findall(r'〈([^〉]+)〉[`]?\s*<@!?(\d+)>', field.value or ''):
            team['players'].append({
                'user_id': int(uid),
                'rank': rank,
            })

        if team['players']:
            teams.append(team)

    return teams if len(teams) >= 2 else None


def parse_text_match(content):
    """Parse a plain-text PUBobot match message to extract teams.

    Fallback parser for non-embed messages.
    """
    teams = []
    current_team = None

    for line in content.split('\n'):
        # Team header: :emoji: TeamName 〈rating〉
        header = re.search(r'(:\w+:).*?([A-Za-z]+).*?〈(\d+)〉', line)
        if header:
            if current_team and current_team['players']:
                teams.append(current_team)
            current_team = {
                'emoji': header.group(1),
                'name': header.group(2),
                'avg_rating': int(header.group(3)),
                'players': [],
            }
            # Players might be on the same line as the header
            for rank, uid in re.findall(r'〈([^〉]+)〉[`]?\s*<@!?(\d+)>', line):
                current_team['players'].append({
                    'user_id': int(uid),
                    'rank': rank,
                })
            continue

        if current_team is not None:
            for rank, uid in re.findall(r'〈([^〉]+)〉[`]?\s*<@!?(\d+)>', line):
                current_team['players'].append({
                    'user_id': int(uid),
                    'rank': rank,
                })

        # Stop at Captains section
        if re.search(r'Captains', line):
            if current_team and current_team['players']:
                teams.append(current_team)
                current_team = None

    if current_team and current_team['players']:
        teams.append(current_team)

    return teams if len(teams) >= 2 else None


def captain_matchmaking(players, ratings):
    """Run captain-based matchmaking algorithm.

    Mirrors the logic from Match.init_teams("captain based matchmaking").

    Args:
        players: list of Player(id, name)
        ratings: dict {player_id: rating}

    Returns:
        (team_a, team_b, captains, method_used)
    """
    team_len = len(players) // 2

    # Regular matchmaking for fallback comparison
    best_rating = sum(ratings[p.id] for p in players) / 2
    regular_team = min(
        combinations(players, team_len),
        key=lambda team: abs(sum(ratings[p.id] for p in team) - best_rating)
    )
    regular_diff = abs(sum(ratings[p.id] for p in regular_team) - best_rating) * 2

    # Top 2 rated = captains
    sorted_players = sorted(players, key=lambda p: ratings[p.id], reverse=True)
    captain_strong = sorted_players[0]
    captain_weak = sorted_players[1]
    remaining = sorted_players[2:]
    remaining_team_len = team_len - 1

    if remaining_team_len == 0:
        # 1v1
        return [captain_strong], [captain_weak], [captain_strong, captain_weak], "captains (1v1)"

    favor_combo = None
    favor_score = float('inf')
    balanced_combo = None
    balanced_diff = float('inf')

    for combo in combinations(remaining, remaining_team_len):
        others = [p for p in remaining if p not in combo][:remaining_team_len]
        strong_elo = ratings[captain_strong.id] + sum(ratings[p.id] for p in combo)
        weak_elo = ratings[captain_weak.id] + sum(ratings[p.id] for p in others)
        diff = weak_elo - strong_elo
        abs_diff = abs(diff)

        if abs_diff < balanced_diff:
            balanced_diff = abs_diff
            balanced_combo = combo

        score = diff if diff >= 0 else abs_diff + 1e6
        if score < favor_score:
            favor_score = score
            favor_combo = combo

    # Step 1: Try weak captain favoring
    favor_actual_diff = abs(
        (ratings[captain_strong.id] + sum(ratings[p.id] for p in favor_combo)) -
        (ratings[captain_weak.id] + sum(ratings[p.id] for p in
            [p for p in remaining if p not in favor_combo][:remaining_team_len]))
    )

    if favor_actual_diff - regular_diff <= 300:
        best_combo = favor_combo
        method = "captain favoring"
    elif balanced_diff - regular_diff <= 300:
        best_combo = balanced_combo
        method = "captain balanced"
    else:
        # Fall back to regular matchmaking
        team_a = sorted(regular_team, key=lambda p: ratings[p.id], reverse=True)
        team_b = sorted(
            [p for p in players if p not in regular_team],
            key=lambda p: ratings[p.id], reverse=True
        )
        return list(team_a), list(team_b), [team_a[0], team_b[0]], "regular matchmaking (fallback)"

    weak_remaining = [p for p in remaining if p not in best_combo][:remaining_team_len]
    team_a = sorted([captain_strong] + list(best_combo), key=lambda p: ratings[p.id], reverse=True)
    team_b = sorted([captain_weak] + weak_remaining, key=lambda p: ratings[p.id], reverse=True)

    return list(team_a), list(team_b), [captain_strong, captain_weak], method
