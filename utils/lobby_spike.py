#!/usr/bin/env python3
"""Phase-0 throwaway spike for the AoE2 lobby feature.

De-risks the ONE unverified surface in docs/aoe2-lobby-replication-plan.md:
the feasibility verdict only proved the socket with `&match_ids=<id>` (you
already know the game id). The auto-search path does NOT know the id — players
create a `test123` lobby themselves — so it must subscribe to the *unfiltered*
`handler=lobbies` feed and match by NAME. That path is unverified. This script
verifies both socket modes plus the REST completion fetch, one per lobby-input
method the feature must support:

  # (1) AUTO-SEARCH BY NAME  -- unfiltered feed, client-side name filter
  python utils/lobby_spike.py watch --name test123 --seconds 180

  # (2)(3) BY GAME ID  -- filtered feed; what /lobby2 <id> and /lobby <id> use
  python utils/lobby_spike.py watch --match-id 123456789 --seconds 180

  # completion fetch (REST by-id) used to resolve the finished match
  python utils/lobby_spike.py rest 123456789

Every raw frame is appended to tests/fixtures/lobby_events.json so the Phase-1
delta reducer + completed-embed renderer get a real golden fixture to test
against.

Throwaway: NOT imported by the bot at runtime. 4-space indent per the utils/
convention. Requires aiohttp (already a bot dependency, >=3.13).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

SOCKET_URL = "wss://socket.aoe2companion.com/listen?handler=lobbies"
REST_BY_ID = "https://data.aoe2companion.com/api/matches/{game_id}"
USER_AGENT = "NammaPUBobot/1.0"  # non-empty UA is mandatory (403 without)

_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "lobby_events.json"


# ── helpers ──────────────────────────────────────────────────────────────

def _walk_names(obj):
    """Yield every value found under a "name" key, anywhere in a nested
    dict/list structure. Used to spot a lobby by name without knowing the
    socket's exact envelope shape (the whole point of the spike)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "name" and isinstance(v, str):
                yield v
            else:
                yield from _walk_names(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_names(item)


def _name_matches(frame, target):
    target = target.lower()
    return any(n.lower() == target for n in _walk_names(frame))


def _summary(frame):
    """Best-effort one-line description of a decoded frame without assuming
    its shape. Prints the event 'type' if present, plus any names seen."""
    parts = []
    if isinstance(frame, dict):
        for key in ("type", "operation", "event", "handler"):
            if key in frame:
                parts.append(f"{key}={frame[key]!r}")
    elif isinstance(frame, list):
        parts.append(f"list[{len(frame)}]")
    names = sorted(set(_walk_names(frame)))
    if names:
        parts.append("names=" + ",".join(names[:6]))
    return " ".join(parts) or "(opaque)"


def _save_fixture(frames, label):
    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"label": label, "captured_at": int(time.time()), "frames": frames}
    _FIXTURE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[fixture] {len(frames)} frame(s) -> {_FIXTURE}")


# ── socket watch ─────────────────────────────────────────────────────────

async def watch(name, match_id, seconds):
    url = SOCKET_URL + (f"&match_ids={match_id}" if match_id else "")
    mode = f"by-id={match_id}" if match_id else f"by-name={name!r} (UNFILTERED feed)"
    print(f"[watch] {mode}  for {seconds}s")
    print(f"[watch] {url}\n")

    frames = []
    matched = 0
    deadline = time.monotonic() + seconds

    try:
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            async with session.ws_connect(url, heartbeat=30) as ws:
                print("[watch] connected - waiting for frames "
                      "(create/join the lobby now if you haven't)...\n")
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        msg = await ws.receive(timeout=remaining)
                    except TimeoutError:
                        break

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            frame = json.loads(msg.data)
                        except ValueError:
                            print(f"  [raw non-json] {msg.data[:120]}")
                            continue
                        # Skip keepalives the doc flagged.
                        if isinstance(frame, dict) and frame.get("type") == "pong":
                            continue
                        frames.append(frame)
                        elapsed = seconds - (deadline - time.monotonic())
                        hit = name and _name_matches(frame, name)
                        if hit:
                            matched += 1
                        flag = " <<< NAME MATCH" if hit else ""
                        print(f"  +{elapsed:6.1f}s  {_summary(frame)}{flag}")
                        if hit:
                            print(json.dumps(frame, indent=4, ensure_ascii=False))
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                        print("[watch] socket closed by server")
                        break
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        print(f"[watch] socket error: {ws.exception()!r}")
                        break
    except aiohttp.ClientError as e:
        print(f"[watch] connection failed: {e!r}")

    print(f"\n[watch] done - {len(frames)} frame(s), {matched} name-match(es)")
    _save_fixture(frames, label=mode)
    if name and not match_id:
        verdict = "DETECTABLE BY NAME [YES]" if matched else "NOT seen by name [NO] (auto-search at risk)"
        print(f"[verdict] unfiltered detect-by-name {name!r}: {verdict}")


# ── REST completion fetch ────────────────────────────────────────────────

async def rest(game_id):
    url = REST_BY_ID.format(game_id=game_id)
    print(f"[rest] GET {url}")
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            print(f"[rest] HTTP {resp.status}")
            text = await resp.text()
            if resp.status != 200:
                print(text[:500])
                return
            data = json.loads(text)
    finished = data.get("finished") if isinstance(data, dict) else None
    started = data.get("started") if isinstance(data, dict) else None
    dur = None
    if started and finished:
        dur = (finished - started) / 60.0
    dur_str = f"{dur:.1f} min" if dur else "n/a"
    print(f"[rest] finished={finished} started={started} duration={dur_str}")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:4000])
    _save_fixture([data], label=f"rest by-id {game_id}")


# ── cli ──────────────────────────────────────────────────────────────────

def main():
    # The lobby feed carries accented player names; force UTF-8 so prints
    # don't die on a Windows cp1252 console.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    p = argparse.ArgumentParser(description="AoE2 lobby socket/REST spike (Phase 0).")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="subscribe to the lobby socket")
    w.add_argument("--name", default=None, help="lobby name to match on the unfiltered feed (e.g. test123)")
    w.add_argument("--match-id", default=None, help="subscribe filtered to one game id instead")
    w.add_argument("--seconds", type=int, default=120, help="how long to listen (default 120)")

    r = sub.add_parser("rest", help="fetch a finished match by id")
    r.add_argument("game_id", help="aoe2 game/match id")

    args = p.parse_args()
    if args.cmd == "watch":
        if not args.name and not args.match_id:
            p.error("watch needs --name or --match-id")
        asyncio.run(watch(args.name, args.match_id, args.seconds))
    elif args.cmd == "rest":
        asyncio.run(rest(args.game_id))


if __name__ == "__main__":
    sys.exit(main())
