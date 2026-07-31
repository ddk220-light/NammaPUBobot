# -*- coding: utf-8 -*-
"""Fetch one match's .aoe2record into the local replay cache.

Called per match by the live ingest (bot/replay_stats/fetch.py wraps every
function here in asyncio.to_thread -- the requests calls are blocking) and, on
demand, by the offline classification pipelines.

  1. resolve a participant profileId via the aoe2companion match API,
  2. download the .aoe2record from aoe.ms (UA Mozilla/5.0; the response is a
     ZIP) with PATIENT exponential backoff on HTTP 429 -- aoe.ms rate-limits
     hard, and per-IP, which is why the caller stops trying other participants
     once it sees a 429.

WHAT WENT WITH THE OFFLINE QUIZ CORPUS. This module used to also carry a
`main()` that walked data/match_id_map.csv and bulk-downloaded ~6 months of
replays, resuming from a git-tracked data/replay_manifest.csv (plus
`window_ids`/`body_stats`, and manifest.py's load/write/pending/is_done). Its
only reason to exist was building the corpus data/replay_quiz.db was compiled
from; the live ingest downloads per match on demand and never read a line of
it. The manifest CSV was that driver's resume state and nothing else, so it is
gone with the driver.

The cache rule itself is NOT part of that bookkeeping and stayed, in cache.py:
data/replays/ is what makes `download_replay` idempotent, and it lives in its
own stdlib-only module so it stays testable on an install with no requests and
no mgz -- exactly what the retired manifest.py said about itself.
"""
import io
import os
import struct
import time
import zipfile
import zlib

import requests
from mgz.util import get_save_version

from utils.replay.cache import CACHE_DIR, is_cached, replay_path

AOE2COMPANION_MATCH = "https://data.aoe2companion.com/api/matches/{gid}"
AOE_MS = "https://aoe.ms/replay/?gameId={gid}&profileId={pid}"
UA_API = {"User-Agent": "NammaPUBobot/1.0"}
UA_DL = {"User-Agent": "Mozilla/5.0"}
BACKOFF = [15, 30, 60, 120]   # seconds, per-file 429 escalation


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
    path = replay_path(gid)
    if is_cached(gid):
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
