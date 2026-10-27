"""Golden candidates and phrases from the Railway implementation, without importing runtime/DB."""
import ast
import collections
import json
import math
import random
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORIES = ROOT / 'nammaoe2bot/features/storylines'

def pure_module(file, extra=None):
    tree = ast.parse(file.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.Assign, ast.FunctionDef))]
    ns = dict(math=math, random=random, time=__import__('time'), Counter=collections.Counter, namedtuple=collections.namedtuple)
    ns.update(extra or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(file), 'exec'), ns)
    return types.SimpleNamespace(**ns)

insights = pure_module(STORIES / 'insights.py')
payoff = pure_module(STORIES / 'payoff.py', {'ti': insights})
class First:
    @staticmethod
    def choice(xs):
        return xs[0]

ids = [str(100000000000000100 + n) for n in range(10)]
rosters = {0: ids[:4], 1: ids[4:8]}
nicks = {p: f'<@{p}>' for p in ids}
teams = [{'name': 'Alpha'}, {'name': 'Beta'}]
scenarios = []
fixed = [ids[:4], ids[4:8]]
recipes = [
    [(fixed, 0)]*5,
    [(fixed, 1)]*7,
    [(fixed, n%2) for n in range(8)],
    [(fixed, 0)]*3 + [(fixed, 1)]*5,
    [(fixed, 0)]*6 + [([ids[:1]+ids[2:4]+ids[8:9],ids[4:8]], 1)]*7,
]
rng = random.Random(7)
for _ in range(12):
    recipe = []
    for _n in range(60):
        shuffled = rng.sample(ids, 8)
        recipe.append(([shuffled[:4], shuffled[4:]], rng.choice([0,1,0,1,None])))
    recipes.append(recipe)
for index, recipe in enumerate(recipes):
    rows, history = [], []
    for mid,(sides,winner) in enumerate(recipe):
        history.append({'teams':sides,'winner':winner})
        rows.extend({'match_id':mid,'user_id':p,'nick':nicks[p],'team':side,'winner':winner} for side,team in enumerate(sides) for p in team)
    hist=insights._index_history(rows)
    candidates=insights._candidates(hist.order,hist.matches,rosters[0],rosters[1])
    expected=[]
    for c in candidates:
        expected.append({**c,'players':sorted(c['players']),'teams':sorted(c['teams']),
            'tease':insights._phrase(c,nicks,teams,rosters,rng=First),
            'won':payoff.payoff_phrase(c,True,nicks,teams,rosters,rng=First),
            'lost':payoff.payoff_phrase(c,False,nicks,teams,rosters,rng=First)})
    scenarios.append({'name':f'legacy-{index}','teams':fixed,'history':history,'expected':expected})
output=ROOT/'cloudflare/tests/fixtures/story-parity.json'
output.write_text(json.dumps(scenarios,ensure_ascii=False,indent=2)+'\n')
print(f'{len(scenarios)} scenarios; types: {sorted({c["type"] for s in scenarios for c in s["expected"]})}')
