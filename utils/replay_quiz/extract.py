#!/usr/bin/env python3
"""Replay -> structured per-match records (the extraction ALGORITHM).

`extract_match(path, resolved, date_map)` returns one dict per match:
  {
    "match":   {aoe2_match_id, map, save_version, duration_s, date, winner_team},
    "players": [ per-player facts: identity, civ, team, winner, eapm, age clicks,
                 first_tc_s, villager splits, military splits, age_reliable ],
    "units":   [ {pnum, identity, civ, unit, category, is_military,
                  total, pre_feudal, pre_castle, pre_imperial} ],   # long
    "techs":   [ {pnum, identity, civ, tech, click_s, phase} ],     # long, first-click
    "buildings":[ {pnum, identity, civ, building, count} ],         # long
  }

Counts are queue-clicks (upper bound). Age threshold = the CLICK (RESEARCH of the
age tech); "before age X when never reached X" counts the whole game. Attribution
via stable profile_id -> data/profile_resolved.csv. Run with PYTHONPATH=.replay_scratch.
"""
import csv
import os

import mgz.model

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NON_MILITARY = ("villager", "fishing ship", "trade cart", "trade cog",
                "transport ship", "hulk", "sheep", "cow", "goat", "turkey", "llama", "pig")
SIEGE = ("ram", "mangonel", "onager", "scorpion", "bombard cannon", "siege tower",
         "trebuchet", "organ gun", "houfnice")
WARSHIP = ("galley", "galleon", "fire ship", "fire galley", "demolition", "longboat",
           "turtle ship", "caravel", "dromon", "cannon galleon")


def classify_unit(name):
    """-> (category, is_military). Raw name is kept by callers for exact queries."""
    n = (name or "").lower().replace("-", " ")
    if not n:
        return ("other", False)
    if "villager" in n:
        return ("villager", False)
    if any(k in n for k in ("fishing ship", "trade cart", "trade cog", "transport ship", "hulk")):
        return ("trade_transport", False)
    if "scout" in n or "light cavalry" in n or "hussar" in n:
        return ("scout", True)
    if "skirmisher" in n:
        return ("skirmisher", True)
    if "cavalry archer" in n:
        return ("cav_archer", True)
    if any(k in n for k in ("spearman", "pikeman", "halberdier")):
        return ("spearman_line", True)
    if any(k in n for k in ("militia", "man at arms", "long swordsman", "two handed swordsman", "champion")):
        return ("militia_line", True)
    if any(k in n for k in ("archer", "crossbow", "arbalest")):
        return ("archer_line", True)
    if any(k in n for k in ("knight", "cavalier", "paladin")):
        return ("knight_line", True)
    if "camel" in n:
        return ("camel_line", True)
    if any(k in n for k in SIEGE):
        return ("siege", True)
    if any(k in n for k in ("monk", "missionary", "imam", "warrior priest")):
        return ("monk", True)
    if any(k in n for k in WARSHIP):
        return ("warship", True)
    if "elephant" in n:
        return ("elephant", True)
    # trained military unit not matched above -> very likely a civ unique unit
    return ("unique_other", True)


def _secs(td):
    return td.total_seconds() if td is not None else None


def _age_of(uptime_age):
    s = str(uptime_age).upper()
    for tok, key in (("FEUDAL", "feudal"), ("CASTLE", "castle"), ("IMPERIAL", "imperial")):
        if tok in s:
            return key
    return None


def extract_match(path, resolved, date_map=None):
    m = mgz.model.parse_match(open(path, "rb"))
    aoe2_id = int(os.path.basename(path).split(".")[0])
    date_map = date_map or {}
    players = {p.number: p for p in m.players}

    # age-up CLICK times (RESEARCH of the age tech) + completion (uptime, secondary)
    age_click = {n: {} for n in players}
    age_done = {n: {} for n in players}
    for u in m.uptimes:
        if u.player and _age_of(u.age):
            age_done[u.player.number].setdefault(_age_of(u.age), _secs(u.timestamp))

    research_first = {}                 # (pnum, tech) -> click_s  (dedup re-clicks)
    queues = {}                         # (pnum, unit) -> [total, pre_f, pre_c, pre_i]
    first_unit = {}                     # (pnum, unit) -> first_s
    builds = {}                         # (pnum, building) -> count
    tc_build_times = {n: [] for n in players}
    deletes = {n: [] for n in players}
    tc_instances = {n: set() for n in players}
    events = []                         # production timeline: one row per timestamped DE_QUEUE click
                                        # (null-timestamp queues are dropped — nowhere to plot them)

    for p in m.players:
        for o in (p.objects or []):
            if "town center" in (getattr(o, "name", "") or "").lower():
                tc_instances[p.number].add(o.instance_id)

    # first pass: capture age-up clicks (needed for the before-age splits)
    for a in m.actions:
        if a.type.name == "RESEARCH" and a.player:
            tech = (a.payload or {}).get("technology")
            pnum = a.player.number
            if tech and (pnum, tech) not in research_first:
                research_first[(pnum, tech)] = _secs(a.timestamp)
                low = tech.lower()
                for tok, key in (("feudal age", "feudal"), ("castle age", "castle"), ("imperial age", "imperial")):
                    if tok in low:
                        age_click[pnum].setdefault(key, _secs(a.timestamp))

    def before(pnum, ts):
        fc = age_click[pnum].get("feudal")
        cc = age_click[pnum].get("castle")
        ic = age_click[pnum].get("imperial")
        return (fc is None or ts < fc, cc is None or ts < cc, ic is None or ts < ic)

    # second pass: production + buildings + deletes (uses age clicks for splits)
    for a in m.actions:
        if not a.player:
            continue
        pnum = a.player.number
        ts = _secs(a.timestamp)
        pl = a.payload or {}
        t = a.type.name
        if t == "DE_QUEUE":
            unit = pl.get("unit")
            amt = pl.get("amount", 1) or 1
            if not unit:
                continue
            key = (pnum, unit)
            rec = queues.setdefault(key, [0, 0, 0, 0])
            rec[0] += amt
            if ts is not None:
                qcat, qmil = classify_unit(unit)
                events.append(dict(player_number=pnum, kind="queue", name=unit, category=qcat,
                                   is_military=qmil, amount=amt, t_s=round(ts)))
            pf, pc, pi = before(pnum, ts)
            if pf:
                rec[1] += amt
            if pc:
                rec[2] += amt
            if pi:
                rec[3] += amt
            if key not in first_unit:
                first_unit[key] = ts
            if unit == "Villager":
                for oid in pl.get("object_ids", []):
                    tc_instances[pnum].add(oid)
        elif t == "BUILD":
            b = pl.get("building")
            if b:
                builds[(pnum, b)] = builds.get((pnum, b), 0) + 1
                if b == "Town Center":
                    tc_build_times[pnum].append(ts)
        elif t == "DELETE":
            deletes[pnum].append((ts, pl.get("object_ids", [])))

    total_uptimes = sum(len(v) for v in age_done.values())
    age_reliable = not (total_uptimes == 0 and _secs(m.duration) and _secs(m.duration) > 600)

    out_players, out_units, out_techs, out_buildings = [], [], [], []
    for pnum, p in players.items():
        nick, aoe2_name, src = resolved.get(p.profile_id, ("", p.name, "unmapped"))
        identity = nick or aoe2_name or p.name
        civ = p.civilization
        fc = age_click[pnum].get("feudal")
        cc = age_click[pnum].get("castle")
        ic = age_click[pnum].get("imperial")
        first_tc = min(tc_build_times[pnum]) if tc_build_times[pnum] else None

        # per-unit rows + aggregate military/villager splits
        vil = [0, 0, 0, 0]
        mil = [0, 0, 0, 0]
        for (qn, unit), rec in queues.items():
            if qn != pnum:
                continue
            cat, is_mil = classify_unit(unit)
            out_units.append(dict(player_number=pnum, identity=identity, civ=civ, unit=unit,
                                  category=cat, is_military=is_mil, total=rec[0],
                                  pre_feudal=rec[1], pre_castle=rec[2], pre_imperial=rec[3]))
            if unit == "Villager":
                for i in range(4):
                    vil[i] += rec[i]
            elif is_mil:
                for i in range(4):
                    mil[i] += rec[i]

        for (bn, building), n in [((k[0], k[1]), v) for k, v in builds.items() if k[0] == pnum]:
            out_buildings.append(dict(player_number=pnum, identity=identity, civ=civ,
                                      building=building, count=n))

        for (tn, tech), click in [((k[0], k[1]), v) for k, v in research_first.items() if k[0] == pnum]:
            phase = "dark"
            if ic and click >= ic:
                phase = "imperial"
            elif cc and click >= cc:
                phase = "castle"
            elif fc and click >= fc:
                phase = "feudal"
            out_techs.append(dict(player_number=pnum, identity=identity, civ=civ,
                                  tech=tech, click_s=round(click) if click else None, phase=phase))

        # confirmed TC relocations
        relo = 0
        tcset = tc_instances[pnum]
        bt = sorted(tc_build_times[pnum])
        for dts, oids in deletes[pnum]:
            if any(o in tcset for o in oids) and any(b > dts for b in bt):
                relo += 1

        tid = p.team_id
        team = "+".join(str(x) for x in sorted(tid)) if isinstance(tid, (list, set, frozenset, tuple)) else tid
        out_players.append(dict(
            player_number=pnum, profile_id=p.profile_id, identity=identity, attribution=src,
            civ=civ, team=team, winner=bool(p.winner) if p.winner is not None else None, eapm=p.eapm,
            feudal_s=round(fc) if fc else None, castle_s=round(cc) if cc else None,
            imperial_s=round(ic) if ic else None, first_tc_s=round(first_tc) if first_tc else None,
            tc_build_s=sorted(round(t) for t in tc_build_times[pnum] if t is not None),
            age_reliable=age_reliable,
            villagers=vil[0], vil_pre_feudal=vil[1], vil_pre_castle=vil[2], vil_pre_imperial=vil[3],
            military=mil[0], mil_pre_feudal=mil[1], mil_pre_castle=mil[2], mil_pre_imperial=mil[3],
            tc_relocations=relo,
        ))

    match = dict(aoe2_match_id=aoe2_id, map=getattr(m.map, "name", ""),
                 save_version=m.save_version, duration_s=round(_secs(m.duration)) if _secs(m.duration) else None,
                 date=date_map.get(aoe2_id, ""), winner_team=None)
    return dict(match=match, players=out_players, units=out_units, techs=out_techs,
                buildings=out_buildings, events=events)


def load_resolved():
    m = {}
    path = os.path.join(ROOT, "data", "profile_resolved.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    m[int(r["profile_id"])] = (r.get("nick") or "", r.get("aoe2_name") or "", r.get("source") or "")
                except ValueError:
                    pass
    return m


def load_date_map():
    m = {}
    path = os.path.join(ROOT, "data", "match_civ_details.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    m.setdefault(int(r["aoe2_match_id"]), r.get("date", ""))
                except (ValueError, KeyError):
                    pass
    return m
