#!/usr/bin/env python3
"""Download this server's replays for the last ~6 months and record parse status.

Match source: data/match_id_map.csv (bot_match_id -> aoe2_match_id). aoe2 ids are
monotonic, so a single id cutoff approximates a date window (>=438,000,000 ~ Dec 2025).

Per match:
  1. resolve a participant profileId via the aoe2companion match API,
  2. download .aoe2record from aoe.ms (UA Mozilla/5.0; response is a ZIP) with
     PATIENT exponential backoff on HTTP 429 (aoe.ms rate-limits hard),
  3. record save_version (header) + whether the body action-stream parses.

Resumable: skips files already cached AND rows already in the manifest. Writes
data/replay_manifest.csv incrementally so a crash/stop loses nothing. aoe.ms
availability is per-match (~65-80%); genuine 404s are recorded as unavailable.

Usage:
    python utils/replay_quiz/download.py [--since-id N] [--limit N] [--space SECS]
"""
import argparse
import csv
import io
import os
import struct
import time
import zipfile
import zlib

import requests

from mgz import fast
from mgz.util import get_save_version

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MATCH_MAP = os.path.join(ROOT, "data", "match_id_map.csv")
CACHE_DIR = os.path.join(ROOT, "data", "replays")
MANIFEST = os.path.join(ROOT, "data", "replay_manifest.csv")

AOE2COMPANION_MATCH = "https://data.aoe2companion.com/api/matches/{gid}"
AOE_MS = "https://aoe.ms/replay/?gameId={gid}&profileId={pid}"
UA_API = {"User-Agent": "NammaPUBobot/1.0"}
UA_DL = {"User-Agent": "Mozilla/5.0"}
BACKOFF = [15, 30, 60, 120]   # seconds, per-file 429 escalation
FIELDS = ["aoe2_match_id", "profile_id", "bytes", "save_version", "body_ops",
          "minutes", "body_parse_ok", "error"]


def window_ids(since_id, limit):
    ids = set()
    with open(MATCH_MAP, newline="") as f:
        for row in csv.DictReader(f):
            try:
                a = int(row["aoe2_match_id"])
            except (ValueError, KeyError):
                continue
            if a >= since_id:
                ids.add(a)
    out = sorted(ids, reverse=True)
    return out[:limit] if limit else out


def load_manifest():
    rows = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, newline="") as f:
            for r in csv.DictReader(f):
                rows[r["aoe2_match_id"]] = r
    return rows


def write_manifest(rows):
    with open(MANIFEST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for k in sorted(rows, key=lambda x: -int(x)):
            w.writerow({c: rows[k].get(c, "") for c in FIELDS})


def resolve_profile_ids(gid):
    try:
        r = requests.get(AOE2COMPANION_MATCH.format(gid=gid), headers=UA_API, timeout=20)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    try:
        m = r.json()
    except ValueError:
        return []
    out = []
    for team in m.get("teams", []) or []:
        for p in team.get("players", []) or team.get("members", []) or []:
            pid = p.get("profileId") or p.get("profile_id")
            if pid:
                out.append((pid, bool(p.get("replay"))))
    out.sort(key=lambda x: (not x[1]))
    return [pid for pid, _ in out]


def download_replay(gid, pid):
    """Returns (path|None, status). Patient 429 backoff."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{gid}.aoe2record")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, "cached"
    for attempt in range(len(BACKOFF) + 1):
        try:
            r = requests.get(AOE_MS.format(gid=gid, pid=pid), headers=UA_DL, timeout=90)
        except requests.RequestException as e:
            return None, f"neterr:{type(e).__name__}"
        if r.status_code == 200:
            content = r.content
            if content[:2] == b"PK":
                try:
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        name = next((n for n in zf.namelist() if n.endswith(".aoe2record")), None)
                        if not name:
                            return None, "no_record_in_zip"
                        content = zf.read(name)
                except zipfile.BadZipFile:
                    return None, "bad_zip"
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(content)
            os.replace(tmp, path)
            return path, "ok"
        if r.status_code == 429 and attempt < len(BACKOFF):
            wait = BACKOFF[attempt]
            try:
                wait = max(wait, int(r.headers.get("Retry-After", "0")))
            except ValueError:
                pass
            print(f"      429 -> wait {wait}s", flush=True)
            time.sleep(wait)
            continue
        return None, f"http_{r.status_code}"
    return None, "429_exhausted"


def read_save_version(path):
    with open(path, "rb") as f:
        head = f.read(8)
        hlen = struct.unpack("<I", head[:4])[0]
        f.seek(0)
        comp = f.read(hlen)[8:]
    dec = zlib.decompressobj(-15).decompress(comp, 64)
    nul = dec.index(b"\x00")
    off = nul + 1
    old = struct.unpack_from("<f", dec, off)[0]
    off += 4
    new = struct.unpack_from("<I", dec, off)[0] if old == -1 else None
    return round(get_save_version(old, new), 2)


def body_stats(path):
    try:
        with open(path, "rb") as h:
            hlen = struct.unpack("<I", h.read(4))[0]
            h.seek(hlen)
            fast.meta(h)
            ts = n = 0
            while True:
                try:
                    op, d = fast.operation(h)
                except EOFError:
                    break
                n += 1
                if op is fast.Operation.SYNC:
                    ts += d[0]
            return n, round(ts / 60000, 1), True, ""
    except Exception as e:
        return 0, 0, False, f"{type(e).__name__}: {str(e)[:60]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-id", type=int, default=438000000, help="aoe2 id cutoff (~Dec 2025)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--space", type=float, default=5.0, help="seconds between successful downloads")
    args = ap.parse_args()

    gids = window_ids(args.since_id, args.limit)
    manifest = load_manifest()
    todo = [g for g in gids if str(g) not in manifest
            or manifest[str(g)].get("error") not in ("", "ok")
            and not os.path.exists(os.path.join(CACHE_DIR, f"{g}.aoe2record"))]
    # simpler: attempt everything not already successfully done
    todo = [g for g in gids if not (str(g) in manifest and manifest[str(g)].get("body_parse_ok") == "True")]
    print(f"Window: {len(gids)} matches (>= id {args.since_id}); to attempt: {len(todo)}; "
          f"already done: {len(gids)-len(todo)}", flush=True)

    dl = parse_ok = unavail = 0
    for i, gid in enumerate(todo, 1):
        pids = resolve_profile_ids(gid)
        path = status = None
        for pid in pids[:4]:
            path, status = download_replay(gid, pid)
            if path:
                break
        used_pid = pid if path else ""
        if not path:
            unavail += 1
            manifest[str(gid)] = dict(aoe2_match_id=gid, profile_id="", bytes=0, save_version="",
                                      body_ops=0, minutes=0, body_parse_ok=False,
                                      error=status or "unavailable")
            print(f"[{i}/{len(todo)}] {gid}: unavailable ({status})", flush=True)
            write_manifest(manifest)
            time.sleep(2)
            continue
        dl += 1
        try:
            sv = read_save_version(path)
        except Exception:
            sv = ""
        ops, mins, ok, err = body_stats(path)
        if ok:
            parse_ok += 1
        manifest[str(gid)] = dict(aoe2_match_id=gid, profile_id=used_pid,
                                  bytes=os.path.getsize(path), save_version=sv, body_ops=ops,
                                  minutes=mins, body_parse_ok=ok, error=err)
        print(f"[{i}/{len(todo)}] {gid}: {os.path.getsize(path)}B save_v={sv} "
              f"body={'OK' if ok else 'FAIL'} {mins}min", flush=True)
        write_manifest(manifest)
        if status != "cached":
            time.sleep(args.space)

    vers = {}
    for r in manifest.values():
        if r.get("save_version"):
            vers[r["save_version"]] = vers.get(r["save_version"], 0) + 1
    print(f"\nThis run: downloaded {dl}, parsed {parse_ok}, unavailable {unavail}.", flush=True)
    print(f"Manifest now has {len(manifest)} matches; save_version spread: "
          f"{dict(sorted(vers.items(), key=lambda x: str(x[0])))}", flush=True)


if __name__ == "__main__":
    main()
