#!/usr/bin/env python3
"""Build the queryable replay-quiz database from all replays in data/replays/.

Repeatable & incremental: per-file extraction is cached (keyed on path+size), so
re-running after more downloads only parses the new files. Produces a SQLite DB
(data/replay_quiz.db) with:

  raw, queryable tables:  matches, facts, units, techs, buildings
  derived for quizzes:    leaderboards (per-identity career avg, ranked)
                          metric_top_games (top-3 single-game performances + refs)
  catalog:                metrics (id, label, category, unit, direction)

A quiz is then: pick a random metric -> read its leaderboard (the answer) +
metric_top_games (the supporting/reference games, with civ + match link).

Run with PYTHONPATH=.replay_scratch:
    python utils/replay_quiz/build_db.py
"""
import csv
import glob
import hashlib
import json
import os
import sqlite3

from extract import extract_match, load_resolved, load_date_map

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPLAYS = os.path.join(ROOT, "data", "replays")
DB = os.path.join(ROOT, "data", "replay_quiz.db")
CACHE = os.path.join(ROOT, "data", ".replay_extract_cache")
MIN_GAMES = 3
# A player must have at least this many standard-map games to appear on leaderboards.
MIN_TOTAL_GAMES = 10
# Players excluded from leaderboards/quizzes (inactive / not currently playing).
EXCLUDE_IDENTITIES = {"tokenbadteam"}
# Alt-account / name-split merges: alias -> canonical identity (confirmed same person).
ALIASES = {
    "PrIMeZ_RanjithACC": "Mr_PrIMeZ_Twitch_YouTube",
    "twitch.tv/thiru42": "Thiru",
}
# Metrics are computed on the server's standard map only, so stats are comparable
# and off-meta/custom maps (Yin Yang, Rage Forest, …) can't inject time anomalies.
# Raw tables keep ALL games; only leaderboards/top-games apply this filter.
STANDARD_MAPS = {"Land Nomad", "Nomad"}
MILITARY_BUILDINGS = ("Barracks", "Archery Range", "Stable", "Castle", "Siege Workshop")
REPLAY_URL = "https://www.aoe2insights.com/match/{id}/"   # best-effort human reference

# Curated tech list for "earliest to click X" metrics (only games where researched).
TECHS = ["Loom", "Wheelbarrow", "Hand Cart", "Heavy Plow", "Horse Collar",
         "Double-Bit Axe", "Bow Saw", "Two-Man Saw", "Gold Mining", "Stone Mining",
         "Bloodlines", "Ballistics", "Husbandry", "Forging", "Fletching",
         "Bodkin Arrow", "Chemistry", "Caravan", "Bracer"]
UNIT_CATEGORIES = ["scout", "skirmisher", "archer_line", "spearman_line", "militia_line",
                   "knight_line", "camel_line", "cav_archer", "siege", "monk",
                   "unique_other", "elephant"]


def cache_key(path):
    st = os.stat(path)
    h = hashlib.md5(f"{path}:{st.st_size}".encode()).hexdigest()
    return os.path.join(CACHE, h + ".json")


def extract_all(resolved, date_map):
    os.makedirs(CACHE, exist_ok=True)
    matches = []
    failed = 0
    for p in sorted(glob.glob(os.path.join(REPLAYS, "*.aoe2record"))):
        ck = cache_key(p)
        if os.path.exists(ck):
            matches.append(json.load(open(ck, encoding="utf-8")))
            continue
        try:
            data = extract_match(p, resolved, date_map)
            json.dump(data, open(ck, "w", encoding="utf-8"))
            matches.append(data)
        except Exception as e:
            failed += 1
            print(f"  parse fail {os.path.basename(p)}: {type(e).__name__}: {str(e)[:50]}")
    return matches, failed


def enrich(matches):
    """Flatten to per-(match_id, player) dicts with uniform metric keys."""
    pp = []  # list of enriched per-player dicts
    for md in matches:
        m = md["match"]
        by_pnum = {}
        for f in md["players"]:
            d = dict(f)
            d["aoe2_match_id"] = m["aoe2_match_id"]
            d["map"] = m["map"]
            d["date"] = m["date"]
            d["mil_buildings"] = 0
            by_pnum[f["player_number"]] = d
        for u in md["units"]:
            d = by_pnum.get(u["player_number"])
            if not d:
                continue
            for span in ("total", "pre_feudal", "pre_castle", "pre_imperial"):
                d[f"cat:{u['category']}:{span}"] = d.get(f"cat:{u['category']}:{span}", 0) + u[span]
                d[f"unit:{u['unit']}:{span}"] = d.get(f"unit:{u['unit']}:{span}", 0) + u[span]
        for b in md["buildings"]:
            d = by_pnum.get(b["player_number"])
            if not d:
                continue
            d[f"bld:{b['building']}"] = d.get(f"bld:{b['building']}", 0) + b["count"]
            if b["building"] in MILITARY_BUILDINGS:
                d["mil_buildings"] += b["count"]
        for t in md["techs"]:
            d = by_pnum.get(t["player_number"])
            if d and t["click_s"] is not None:
                k = f"tech:{t['tech']}"
                if k not in d or t["click_s"] < d[k]:
                    d[k] = t["click_s"]
        pp.extend(by_pnum.values())
    return pp


def build_metrics():
    """Declarative catalog. value=dict key in enriched pp; null='zero'(counts) or 'skip'(timing)."""
    M = []
    # C1 villagers
    M += [
        dict(id="vil_total", label="Most villagers / game", cat="Villagers", key="villagers", dir="max", null="zero", unit="count"),
        dict(id="vil_pre_feudal", label="Most villagers before Feudal", cat="Villagers", key="vil_pre_feudal", dir="max", null="zero", unit="count"),
        dict(id="vil_pre_castle", label="Most villagers before Castle", cat="Villagers", key="vil_pre_castle", dir="max", null="zero", unit="count"),
        dict(id="vil_pre_imperial", label="Most villagers before Imperial", cat="Villagers", key="vil_pre_imperial", dir="max", null="zero", unit="count"),
    ]
    # C2 age speed + first TC
    M += [
        dict(id="feudal_fast", label="Fastest to click Feudal", cat="Age speed", key="feudal_s", dir="min", null="skip", unit="seconds"),
        dict(id="castle_fast", label="Fastest to click Castle", cat="Age speed", key="castle_s", dir="min", null="skip", unit="seconds"),
        dict(id="imperial_fast", label="Fastest to click Imperial", cat="Age speed", key="imperial_s", dir="min", null="skip", unit="seconds"),
        dict(id="first_tc_fast", label="Fastest first Town Center", cat="Age speed", key="first_tc_s", dir="min", null="skip", unit="seconds"),
    ]
    # C3 buildings
    M += [
        dict(id="tc_count", label="Most Town Centers", cat="Buildings", key="bld:Town Center", dir="max", null="zero", unit="count"),
        dict(id="mil_buildings", label="Most military buildings", cat="Buildings", key="mil_buildings", dir="max", null="zero", unit="count"),
        dict(id="barracks", label="Most Barracks", cat="Buildings", key="bld:Barracks", dir="max", null="zero", unit="count"),
        dict(id="ranges", label="Most Archery Ranges", cat="Buildings", key="bld:Archery Range", dir="max", null="zero", unit="count"),
        dict(id="stables", label="Most Stables", cat="Buildings", key="bld:Stable", dir="max", null="zero", unit="count"),
        dict(id="castles", label="Most Castles", cat="Buildings", key="bld:Castle", dir="max", null="zero", unit="count"),
    ]
    # C4 military aggregate
    M += [
        dict(id="mil_total", label="Biggest army (military / game)", cat="Military", key="military", dir="max", null="zero", unit="count"),
        dict(id="mil_pre_feudal", label="Most military before Feudal", cat="Military", key="mil_pre_feudal", dir="max", null="zero", unit="count"),
        dict(id="mil_pre_castle", label="Most military before Castle", cat="Military", key="mil_pre_castle", dir="max", null="zero", unit="count"),
        dict(id="mil_pre_imperial", label="Most military before Imperial", cat="Military", key="mil_pre_imperial", dir="max", null="zero", unit="count"),
        dict(id="mil_pre_imperial_low", label="Fewest military before Imperial", cat="Military", key="mil_pre_imperial", dir="min", null="zero", unit="count", mingames=4),
    ]
    # C4 by unit type
    PRETTY = {"scout": "scouts", "skirmisher": "skirmishers", "archer_line": "archers",
              "spearman_line": "spearmen", "militia_line": "militia-line", "knight_line": "knights",
              "camel_line": "camels", "cav_archer": "cavalry archers", "siege": "siege",
              "monk": "monks", "unique_other": "unique units", "elephant": "elephants"}
    LAB = {"pre_feudal": "before Feudal", "pre_castle": "before Castle", "pre_imperial": "before Imperial"}
    # whole-game totals per unit type (always sensible)
    for cat in UNIT_CATEGORIES:
        M.append(dict(id=f"{cat}_total", label=f"Most {PRETTY[cat]} (per game)", cat="Military by type",
                      key=f"cat:{cat}:total", dir="max", null="zero", unit="count"))
    # only the unit/age pairings that make game sense (a unit can exist by that age)
    SENSIBLE = [("scout", "pre_feudal"), ("militia_line", "pre_feudal"),
                ("scout", "pre_castle"), ("militia_line", "pre_castle"), ("archer_line", "pre_castle"),
                ("skirmisher", "pre_castle"), ("spearman_line", "pre_castle"),
                ("knight_line", "pre_imperial"), ("camel_line", "pre_imperial"), ("archer_line", "pre_imperial"),
                ("cav_archer", "pre_imperial"), ("siege", "pre_imperial"),
                ("unique_other", "pre_imperial"), ("elephant", "pre_imperial")]
    for cat, span in SENSIBLE:
        M.append(dict(id=f"{cat}_{span}", label=f"Most {PRETTY[cat]} {LAB[span]}", cat="Military by type",
                      key=f"cat:{cat}:{span}", dir="max", null="zero", unit="count"))
    # C5 tech timing
    for tech in TECHS:
        M.append(dict(id=f"tech_{tech.lower().replace(' ', '_').replace('-', '_')}",
                      label=f"Earliest to click {tech}", cat="Tech timing",
                      key=f"tech:{tech}", dir="min", null="skip", unit="seconds"))
    return M


def main():
    resolved = load_resolved()
    date_map = load_date_map()
    matches, failed = extract_all(resolved, date_map)
    print(f"matches parsed: {len(matches)} ({failed} failed)")
    # apply alt-account merges to every record's identity before anything downstream
    for md in matches:
        for rec in md["players"] + md["units"] + md["techs"] + md["buildings"]:
            rec["identity"] = ALIASES.get(rec["identity"], rec["identity"])
    pp = enrich(matches)
    print(f"player-games: {len(pp)}")

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.executescript("""
        DROP TABLE IF EXISTS matches; DROP TABLE IF EXISTS facts; DROP TABLE IF EXISTS units;
        DROP TABLE IF EXISTS techs; DROP TABLE IF EXISTS buildings; DROP TABLE IF EXISTS leaderboards;
        DROP TABLE IF EXISTS metric_top_games; DROP TABLE IF EXISTS metrics; DROP TABLE IF EXISTS players;
        CREATE TABLE matches(aoe2_match_id INT PRIMARY KEY, map TEXT, save_version REAL, duration_s INT, date TEXT, replay_url TEXT);
        CREATE TABLE facts(aoe2_match_id INT, player_number INT, identity TEXT, profile_id INT, attribution TEXT,
            civ TEXT, team INT, winner INT, eapm REAL, feudal_s INT, castle_s INT, imperial_s INT, first_tc_s INT,
            age_reliable INT, villagers INT, vil_pre_feudal INT, vil_pre_castle INT, vil_pre_imperial INT,
            military INT, mil_pre_feudal INT, mil_pre_castle INT, mil_pre_imperial INT, tc_relocations INT);
        CREATE TABLE units(aoe2_match_id INT, player_number INT, identity TEXT, civ TEXT, unit TEXT, category TEXT,
            is_military INT, total INT, pre_feudal INT, pre_castle INT, pre_imperial INT);
        CREATE TABLE techs(aoe2_match_id INT, player_number INT, identity TEXT, civ TEXT, tech TEXT, click_s INT, phase TEXT);
        CREATE TABLE buildings(aoe2_match_id INT, player_number INT, identity TEXT, civ TEXT, building TEXT, count INT);
        CREATE TABLE leaderboards(metric_id TEXT, label TEXT, category TEXT, direction TEXT, unit TEXT,
            rank INT, identity TEXT, avg_value REAL, n_games INT);
        CREATE TABLE metric_top_games(metric_id TEXT, rank INT, aoe2_match_id INT, identity TEXT, civ TEXT,
            value REAL, map TEXT, date TEXT, replay_url TEXT);
        CREATE TABLE metrics(id TEXT PRIMARY KEY, label TEXT, category TEXT, direction TEXT, unit TEXT);
        CREATE TABLE players(identity TEXT PRIMARY KEY, user_id TEXT, rating INT, deviation INT, games INT);
    """)
    for md in matches:
        m = md["match"]
        c.execute("INSERT OR REPLACE INTO matches VALUES(?,?,?,?,?,?)",
                  (m["aoe2_match_id"], m["map"], m["save_version"], m["duration_s"], m["date"],
                   REPLAY_URL.format(id=m["aoe2_match_id"])))
        for f in md["players"]:
            c.execute("INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (m["aoe2_match_id"], f["player_number"], f["identity"], f["profile_id"], f["attribution"],
                       f["civ"], f["team"], int(bool(f["winner"])) if f["winner"] is not None else None, f["eapm"],
                       f["feudal_s"], f["castle_s"], f["imperial_s"], f["first_tc_s"], int(f["age_reliable"]),
                       f["villagers"], f["vil_pre_feudal"], f["vil_pre_castle"], f["vil_pre_imperial"],
                       f["military"], f["mil_pre_feudal"], f["mil_pre_castle"], f["mil_pre_imperial"], f["tc_relocations"]))
        for u in md["units"]:
            c.execute("INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (m["aoe2_match_id"], u["player_number"], u["identity"], u["civ"], u["unit"], u["category"],
                       int(u["is_military"]), u["total"], u["pre_feudal"], u["pre_castle"], u["pre_imperial"]))
        for t in md["techs"]:
            c.execute("INSERT INTO techs VALUES(?,?,?,?,?,?,?)",
                      (m["aoe2_match_id"], t["player_number"], t["identity"], t["civ"], t["tech"], t["click_s"], t["phase"]))
        for b in md["buildings"]:
            c.execute("INSERT INTO buildings VALUES(?,?,?,?,?,?)",
                      (m["aoe2_match_id"], b["player_number"], b["identity"], b["civ"], b["building"], b["count"]))

    # match ref lookup for top-games
    mref = {md["match"]["aoe2_match_id"]: md["match"] for md in matches}
    civ_in = {(pp_["aoe2_match_id"], pp_["identity"]): pp_["civ"] for pp_ in pp}

    # total standard-map games per identity (a "regular" plays >=3); used to keep
    # one-off guests off leaderboards while still allowing rare-event averages.
    total_games = {}
    for d in pp:
        if d.get("map") in STANDARD_MAPS and d["identity"] not in EXCLUDE_IDENTITIES:
            total_games[d["identity"]] = total_games.get(d["identity"], 0) + 1

    metrics = build_metrics()
    for met in metrics:
        c.execute("INSERT INTO metrics VALUES(?,?,?,?,?)", (met["id"], met["label"], met["cat"], met["dir"], met["unit"]))
        key, direction, null = met["key"], met["dir"], met["null"]
        min_qual = met.get("mingames", 2)   # games where the player actually did it
        # per-game values
        games = []  # (match_id, identity, value)
        by_ident = {}
        # age-threshold + age-speed metrics are only meaningful in real laddered games
        # (a feudal click exists); exclude full-tech/truncated games where age data is absent.
        age_gated = ("pre_" in key) or (met["cat"] in ("Age speed", "Tech timing"))
        # for "most X" count metrics, average ONLY over games where the player actually
        # did it (value > 0) — so e.g. "most military before Feudal" reads >=1, not 0.17
        # (a game with 0 isn't evidence about how much they make when they go for it).
        exclude_zeros = (met["unit"] == "count" and direction == "max")
        for d in pp:
            if d["identity"] in EXCLUDE_IDENTITIES:
                continue
            if d.get("map") not in STANDARD_MAPS:
                continue
            if age_gated and (not d.get("age_reliable", 1) or d.get("feudal_s") is None):
                continue
            if null == "skip":
                v = d.get(key)
                if v is None:
                    continue
            else:
                v = d.get(key, 0)
            if exclude_zeros and not v:
                continue
            games.append((d["aoe2_match_id"], d["identity"], v))
            by_ident.setdefault(d["identity"], []).append(v)
        # leaderboard (career average)
        board = []
        for ident, vals in by_ident.items():
            if total_games.get(ident, 0) >= MIN_TOTAL_GAMES and len(vals) >= min_qual:
                board.append((ident, sum(vals) / len(vals), len(vals)))
        board.sort(key=lambda x: x[1], reverse=(direction == "max"))
        for rank, (ident, avg, n) in enumerate(board[:40], 1):
            c.execute("INSERT INTO leaderboards VALUES(?,?,?,?,?,?,?,?,?)",
                      (met["id"], met["label"], met["cat"], direction, met["unit"], rank, ident, round(avg, 2), n))
        # top-3 single-game performances (the reference games)
        games.sort(key=lambda x: x[2], reverse=(direction == "max"))
        for rank, (mid, ident, val) in enumerate(games[:3], 1):
            r = mref.get(mid, {})
            c.execute("INSERT INTO metric_top_games VALUES(?,?,?,?,?,?,?,?,?)",
                      (met["id"], rank, mid, ident, civ_in.get((mid, ident), ""), val,
                       r.get("map", ""), r.get("date", ""), REPLAY_URL.format(id=mid)))

    # players table: identity -> current Elo (via profile_resolved user_id -> qc_players rating)
    uid_rating = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "data", "qc_players.csv"), encoding="utf-8")):
        try:
            uid_rating[r["user_id"]] = (int(r["rating"]), int(r["deviation"]))
        except (ValueError, KeyError):
            pass
    ident_uid = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "data", "profile_resolved.csv"), encoding="utf-8")):
        ident = r["nick"] or r["aoe2_name"]
        if ident:
            ident_uid[ident] = r["user_id"]
    games_per = {}
    for d in pp:
        if d.get("map") in STANDARD_MAPS:
            games_per[d["identity"]] = games_per.get(d["identity"], 0) + 1
    for ident, n in games_per.items():
        if ident in EXCLUDE_IDENTITIES:
            continue
        uid = ident_uid.get(ident, "")
        rat, dev = uid_rating.get(uid, (None, None))
        c.execute("INSERT OR REPLACE INTO players VALUES(?,?,?,?,?)", (ident, uid, rat, dev, n))

    conn.commit()
    # summary
    print(f"\nDB written: {DB}")
    for tbl in ("matches", "facts", "units", "techs", "buildings", "leaderboards", "metric_top_games", "metrics"):
        print(f"  {tbl}: {c.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]} rows")

    # demo: 3 sample quiz questions straight from the DB
    print("\n=== sample quiz questions (random metrics) ===")
    import random
    random.seed(7)
    ids = [r[0] for r in c.execute("SELECT id FROM metrics").fetchall()]
    for mid in random.sample(ids, 5):
        lab, direction, unit = c.execute("SELECT label,direction,unit FROM metrics WHERE id=?", (mid,)).fetchone()
        lb = c.execute("SELECT identity,avg_value,n_games FROM leaderboards WHERE metric_id=? ORDER BY rank LIMIT 3", (mid,)).fetchall()
        if not lb:
            continue
        def fmt(v):
            return f"{int(v)//60}:{int(v)%60:02d}" if unit == "seconds" else f"{v:g}"
        print(f"\nQ: {lab}? (career avg, >= {MIN_GAMES} games)")
        for ident, val, n in lb:
            print(f"    {ident:18} {fmt(val)}  (n={n})")
        tg = c.execute("SELECT identity,civ,value,map,aoe2_match_id FROM metric_top_games WHERE metric_id=? ORDER BY rank", (mid,)).fetchall()
        print("   top games:", "; ".join(f"{i} ({civ}, {fmt(v)}, {mp}, #{mid_})" for i, civ, v, mp, mid_ in tg))
    conn.close()


if __name__ == "__main__":
    main()
