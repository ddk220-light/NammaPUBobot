"""Generate compatibility evidence from isolated old calculation methods, without booting the bot or reading its database."""

import ast
import itertools
import json
import random
from pathlib import Path
from types import SimpleNamespace

root = Path.cwd().parent
rating_source = (root / "nammaoe2bot/pickup/rating.py").read_text()
module = ast.parse(rating_source)
base = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "BaseRating")
keep = {"__init__", "_scale_win", "_scale_loss", "_scale_draw", "_scale_changes"}
base.body = [n for n in base.body if isinstance(n, ast.FunctionDef) and n.name in keep]
aoe = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "AoE2Rating")
namespace = {}
exec(
    compile(
        ast.fix_missing_locations(ast.Module(body=[base, aoe], type_ignores=[])),
        "<isolated legacy rating methods>",
        "exec",
    ),
    namespace,
)
legacy = namespace["AoE2Rating"]
settings = dict(
    initial=1000,
    deviation=200,
    minDeviation=100,
    scale=100,
    winScale=100,
    lossScale=100,
    drawBonus=0,
    winStreakBoost=False,
    lossStreakBoost=False,
)
rng = random.Random(741)


def player(i, rating=1000):
    return dict(
        id=str(100000000000000100 + i),
        name=f"Player {i}",
        rating=rating,
        deviation=200,
        wins=rng.randrange(20),
        losses=rng.randrange(20),
        draws=rng.randrange(3),
        streak=rng.randrange(-6, 7),
    )


fixtures = []
for case in range(60):
    n = [1, 2, 4, 8][case % 4]
    teams = [[player(i + side * n, rng.randrange(0, 4001)) for i in range(n)] for side in range(2)]
    if case < 3:
        teams = [[dict(player(side), rating=1000, wins=0, losses=0, draws=0, streak=0)] for side in range(2)]
    s = settings.copy()
    if case % 2:
        s.update(scale=125, winScale=90, lossScale=110, drawBonus=25, winStreakBoost=True, lossStreakBoost=True)
    if case == 59:
        s.update(scale=0, winScale=0, lossScale=0)
    rating = legacy(
        channel_id=0,
        init_rp=s["initial"],
        init_deviation=s["deviation"],
        min_deviation=s["minDeviation"],
        scale=s["scale"],
        win_scale=s["winScale"],
        loss_scale=s["lossScale"],
        draw_bonus=s["drawBonus"],
        ws_boost=s["winStreakBoost"],
        ls_boost=s["lossStreakBoost"],
    )
    winner = [0, 1, None][case % 3]
    if winner == 1:
        reverse = rating.rate(teams[1], teams[0])
        expected = [reverse[1], reverse[0]]
    else:
        expected = rating.rate(teams[0], teams[1], draw=winner is None)
    fixtures.append(dict(teams=teams, winner=winner, settings=s, expected=expected))

source = ast.parse((root / "nammaoe2bot/pickup/match/match.py").read_text())
match = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "Match")
method = next(n for n in match.body if isinstance(n, ast.FunctionDef) and n.name == "init_teams")
cls = ast.ClassDef(name="LegacyBalance", bases=[], keywords=[], body=[method], decorator_list=[])
ns = dict(combinations=itertools.combinations)
exec(
    compile(
        ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), "<isolated legacy balancing method>", "exec"
    ),
    ns,
)


class Team(list):
    def set(self, items):
        self[:] = items


balances = []
unrestricted = []
for case in range(28):
    n = [2, 4, 6, 8, 12, 16, 4][case % 7]
    players = [dict(player(i), rating=rng.randrange(400, 2001)) for i in range(n)]
    if case == 0:
        for p in players:
            p["rating"] = 1000
    if case == 6:
        for p, v in zip(players, [4000, 3950, 500, 0]):
            p["rating"] = v
    old = ns["LegacyBalance"]()
    old.players = [SimpleNamespace(id=p["id"], roles=[]) for p in players]
    old.ratings = {p["id"]: p["rating"] for p in players}
    old.cfg = {"team_size": n // 2, "captains_role_id": None}
    old.teams = [Team(), Team(), Team()]
    old.sort_players = lambda team, ratings=old.ratings: sorted(
        team, key=lambda member: ratings[member.id], reverse=True
    )
    old.init_teams("captain based matchmaking")
    balances.append(dict(players=players, expected=[[p.id for p in t] for t in old.teams[:2]]))
    old.init_teams("matchmaking")
    unrestricted.append(dict(players=players, expected=[[p.id for p in t] for t in old.teams[:2]]))
Path("tests/fixtures/legacy-m2.json").write_text(
    json.dumps(
        dict(
            source="nammaoe2bot/pickup/rating.py and pickup/match/match.py; AST-isolated calculation methods; seed 741",
            rating=fixtures,
            balance=balances,
            unrestricted=unrestricted,
        ),
        indent=2,
    )
    + "\n"
)
print(f"Generated {len(fixtures)} rating and {len(balances)} balance comparisons from the legacy implementation.")
