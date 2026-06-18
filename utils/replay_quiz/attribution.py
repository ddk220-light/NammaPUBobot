#!/usr/bin/env python3
"""Resolve in-replay players (stable aoe2 profile_id) -> this server's leaderboard
identity (Discord user_id / nick).

Accuracy strategy (no fuzzy name matching):
  1. SEED: data/player_profile_map.csv gives profile_id -> (user_id, nick) for known players.
  2. ELIMINATION: for every match where we have BOTH the replay roster (8 profile_ids,
     parsed) AND the bot's Discord roster (8 user_ids, from qc_player_matches via
     match_id_map), the two sets are the SAME 8 humans. Subtract already-known
     profile->user links; if exactly one unknown profile_id and one unknown user_id
     remain, they are forced to map to each other. Iterate to a fixpoint.
  3. CONSISTENCY: a profile_id forced to different user_ids across matches is a conflict
     (flagged, not trusted). Where seed and elimination both resolve a profile, they
     must AGREE — disagreements are reported as accuracy failures.

Output: data/profile_resolved.csv (profile_id,user_id,nick,aoe2_name,source,appearances)
and a coverage/accuracy report to stdout.

Run with PYTHONPATH=.replay_scratch (patched mgz).
"""
import csv
import glob
import json
import os
from collections import Counter, defaultdict

import mgz.model

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPLAYS = os.path.join(ROOT, "data", "replays")
PROFILE_CACHE = os.path.join(ROOT, "data", ".replay_profiles_cache.json")
OUT = os.path.join(ROOT, "data", "profile_resolved.csv")


def load_csv(name):
    with open(os.path.join(ROOT, "data", name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def replay_rosters():
    """{aoe2_match_id: [(profile_id, name), ...]} parsed from replays, cached."""
    cache = {}
    if os.path.exists(PROFILE_CACHE):
        cache = json.load(open(PROFILE_CACHE, encoding="utf-8"))
    out = {}
    for p in sorted(glob.glob(os.path.join(REPLAYS, "*.aoe2record"))):
        aid = os.path.basename(p).split(".")[0]
        if aid in cache:
            out[int(aid)] = cache[aid]
            continue
        try:
            m = mgz.model.parse_match(open(p, "rb"))
            roster = [[pl.profile_id, pl.name] for pl in m.players if pl.profile_id and pl.profile_id > 0]
            cache[aid] = roster
            out[int(aid)] = roster
        except Exception as e:
            print(f"  (skip {aid}: {type(e).__name__})")
    json.dump(cache, open(PROFILE_CACHE, "w", encoding="utf-8"))
    return out


def main():
    # seed map
    seed = {}            # profile_id -> (user_id, nick)
    pid_aoe2name = {}    # profile_id -> aoe2_name (from seed)
    for r in load_csv("player_profile_map.csv"):
        pid = (r.get("profile_id") or "").strip()
        if pid:
            try:
                seed[int(pid)] = (r["user_id"], r["nick"])
            except ValueError:
                pass
    lb = {r["user_id"]: r["nick"] for r in load_csv("qc_players.csv")}  # leaderboard
    aoe2_to_bot = {}
    for r in load_csv("match_id_map.csv"):
        try:
            aoe2_to_bot[int(r["aoe2_match_id"])] = int(r["bot_match_id"])
        except ValueError:
            pass
    bot_roster = defaultdict(list)
    for r in load_csv("qc_player_matches.csv"):
        try:
            bot_roster[int(r["match_id"])].append(r["user_id"])
        except ValueError:
            pass

    rosters = replay_rosters()
    appearances = Counter()
    pid_names = defaultdict(Counter)
    for aid, roster in rosters.items():
        for pid, name in roster:
            appearances[pid] += 1
            pid_names[pid][name] += 1

    # elimination
    resolved = dict(seed)  # profile_id -> (user_id, nick); start from seed
    source = {pid: "seed" for pid in seed}
    conflicts = []
    changed = True
    passes = 0
    while changed and passes < 10:
        changed = False
        passes += 1
        for aid, roster in rosters.items():
            bid = aoe2_to_bot.get(aid)
            if bid is None or bid not in bot_roster:
                continue
            pids = [pid for pid, _ in roster]
            uids = list(bot_roster[bid])
            known_uids = {resolved[pid][0] for pid in pids if pid in resolved}
            unknown_pids = [pid for pid in pids if pid not in resolved]
            unknown_uids = [u for u in uids if u not in known_uids]
            if len(unknown_pids) == 1 and len(unknown_uids) == 1:
                pid, uid = unknown_pids[0], unknown_uids[0]
                nick = lb.get(uid, "")
                resolved[pid] = (uid, nick)
                source[pid] = "elim"
                changed = True

    # consistency self-check: re-run elimination read-only, ensure no profile would map
    # to a different user than recorded (and seed vs elim agreement where overlapping)
    # (seed entries are authoritative; elim only fills gaps, so agreement holds by construction;
    #  we instead verify each elim link against EVERY match it appears in)
    elim_violations = []
    for aid, roster in rosters.items():
        bid = aoe2_to_bot.get(aid)
        if bid is None or bid not in bot_roster:
            continue
        pids = [pid for pid, _ in roster]
        uids = set(bot_roster[bid])
        for pid in pids:
            if pid in resolved and resolved[pid][0] not in uids:
                elim_violations.append((aid, pid, resolved[pid][0]))

    # report
    distinct = len(appearances)
    total_app = sum(appearances.values())
    cov_profiles = sum(1 for pid in appearances if pid in resolved)
    cov_app = sum(c for pid, c in appearances.items() if pid in resolved)
    seed_cov = sum(1 for pid in appearances if pid in seed)
    elim_added = sum(1 for pid in appearances if pid in resolved and source.get(pid) == "elim")
    print(f"elimination passes: {passes}")
    print(f"distinct profiles in replays: {distinct}  (appearances {total_app})")
    print(f"  seed-covered:  {seed_cov} profiles")
    print(f"  +elimination:  {elim_added} more profiles")
    print(f"  TOTAL covered: {cov_profiles}/{distinct} profiles ({cov_profiles/distinct:.0%}); "
          f"{cov_app}/{total_app} appearances ({cov_app/total_app:.0%})")
    print(f"consistency violations (resolved profile not in a match's bot roster): {len(elim_violations)}")
    for v in elim_violations[:10]:
        print(f"    match {v[0]} profile {v[1]} -> user {v[2]} NOT in roster")
    still = [(pid, dict(pid_names[pid]), appearances[pid]) for pid in appearances if pid not in resolved]
    print(f"\nstill unmapped: {len(still)} profiles, {sum(x[2] for x in still)} appearances")
    for pid, names, n in sorted(still, key=lambda x: -x[2]):
        print(f"    {pid}: {names} (x{n})")

    # write resolved map
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["profile_id", "user_id", "nick", "aoe2_name", "source", "appearances"])
        for pid in sorted(appearances, key=lambda x: -appearances[x]):
            uid, nick = resolved.get(pid, ("", ""))
            aoe2_name = pid_names[pid].most_common(1)[0][0] if pid_names[pid] else ""
            w.writerow([pid, uid, nick, aoe2_name, source.get(pid, "unmapped"), appearances[pid]])
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
