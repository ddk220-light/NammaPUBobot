"""Offline rating compatibility fixtures; run with glicko2 2.1.0 and trueskill 0.4.5."""
import ast
import json
import random
from pathlib import Path
import glicko2
import trueskill

tree = ast.parse((Path.cwd().parent / 'nammaoe2bot/pickup/rating.py').read_text())
classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
base = next(n for n in classes if n.name == 'BaseRating')
keep = {'__init__', '_scale_win', '_scale_loss', '_scale_draw', '_scale_changes'}
base.body = [n for n in base.body if isinstance(n, ast.FunctionDef) and n.name in keep]
ns = dict(glicko2=glicko2, trueskill=trueskill)
exec(compile(ast.fix_missing_locations(ast.Module(body=classes, type_ignores=[])), '<legacy calculation methods>', 'exec'), ns)
rng = random.Random(7414)
cases = []
for engine, name in [('flat','FlatRating'),('glicko2','Glicko2Rating'),('trueskill','TrueSkillRating'),('aoe2','AoE2Rating')]:
    for case in range(90):
        sizes = [(1,1),(2,2),(4,4),(8,8),(1,3)][case % 5]
        teams = [[dict(id=str(100000000000000100+side*10+i),name=f'Player {side*10+i}',rating=rng.randrange(600,1800),deviation=rng.randrange(100,201),wins=rng.randrange(20),losses=rng.randrange(20),draws=rng.randrange(4),streak=rng.randrange(-6,7)) for i in range(n)] for side,n in enumerate(sizes)]
        s = dict(initial=1000,deviation=200,minDeviation=100,scale=100,winScale=100,lossScale=100,drawBonus=0,winStreakBoost=False,lossStreakBoost=False)
        if case%2: s.update(scale=125,winScale=90,lossScale=110,drawBonus=25,winStreakBoost=True,lossStreakBoost=True)
        if case==89: s.update(scale=0,winScale=0,lossScale=0)
        model = ns[name](channel_id=0,init_rp=s['initial'],init_deviation=s['deviation'],min_deviation=s['minDeviation'],scale=s['scale'],win_scale=s['winScale'],loss_scale=s['lossScale'],draw_bonus=s['drawBonus'],ws_boost=s['winStreakBoost'],ls_boost=s['lossStreakBoost'])
        winner = [0,1,None][case%3]
        if winner==1:
            reverse=model.rate(teams[1],teams[0]); expected=[reverse[1],reverse[0]]
        else: expected=model.rate(teams[0],teams[1],draw=winner is None)
        cases.append(dict(engine=engine,teams=teams,winner=winner,settings=s,expected=expected))
Path('tests/fixtures/legacy-m4.json').write_text(json.dumps(dict(source='AST-isolated legacy methods; glicko2 2.1.0; trueskill 0.4.5; seed 7414',rating=cases),indent=2)+'\n')
print(f'Generated {len(cases)} legacy comparisons.')
