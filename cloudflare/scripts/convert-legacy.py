"""Offline, bounded legacy CSV/ZIP conversion. Never connects to a database or deploys."""

import argparse
import csv
import io
import json
import zipfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

CORE_FILES = {
    "qc_players.csv",
    "qc_matches.csv",
    "qc_player_matches.csv",
    "qc_rating_history.csv",
    "player_ratings.csv",
    "matches.csv",
    "match_players.csv",
    "rating_history.csv",
}


def read_files(path):
    path = Path(path)
    if path.is_dir():
        files = {}
        for p in path.glob("*.csv"):
            if p.name not in CORE_FILES:
                continue
            if p.stat().st_size > 5_000_000:
                raise ValueError(f"{p.name} exceeds 5 MB.")
            files[p.name] = p.read_bytes()
        if sum(map(len, files.values())) > 25_000_000:
            raise ValueError("CSV snapshot exceeds 25 MB.")
    else:
        if path.stat().st_size > 5_000_000:
            raise ValueError("ZIP exceeds 5 MB.")
        with zipfile.ZipFile(path) as z:
            entries = [e for e in z.infolist() if not e.is_dir()]
            if len(entries) > 40 or sum(e.file_size for e in entries) > 25_000_000:
                raise ValueError("Archive exceeds 40 files or 25 MB expanded.")
            if any(e.flag_bits & 1 for e in entries):
                raise ValueError("Encrypted archives are unsupported.")
            files = {}
            for e in entries:
                name = e.filename.replace("\\", "/").rsplit("/", 1)[-1]
                if name in CORE_FILES:
                    if name in files:
                        raise ValueError("Duplicate CSV filename.")
                    files[name] = z.read(e)
    out = {}
    for name, data in files.items():
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        if len(rows) > 8000:
            raise ValueError(f"{name} exceeds 8,000 rows.")
        out[name] = rows
    return out


def integer(row, key, default=None):
    value = row.get(key)
    return default if value is None or str(value).strip() in ("", "NULL") else int(value)


def convert(files, source, mapping, mode, source_id, tz_name, null_result):
    mappings = mapping["mappings"]
    if len({m["ladder"] for m in mappings}) != len(mappings):
        raise ValueError("Duplicate ladder mapping.")
    pubo = source == "pubobot"
    if pubo and len(mappings) != 1:
        raise ValueError("A PUBobot export has one source ladder; map its queues to that ladder.")
    channel_map = mapping.get("ladderByChannel", {})

    def ladder(row):
        if pubo:
            return mappings[0]["ladder"]
        key = str(row.get("channel_id", ""))
        if key not in channel_map:
            raise ValueError(f"Source channel {key} has no explicit ladder mapping.")
        return channel_map[key]

    def timestamp(value):
        if value is None or str(value).strip() in ("", "NULL"):
            return None
        if not pubo:
            return int(value) * 1000
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            if not tz_name:
                raise ValueError("PUBobot timestamps require --timezone, for example UTC.")
            # A wall time that changes offsets across folds is ambiguous; do not silently guess.
            zone = ZoneInfo(tz_name)
            if dt.replace(tzinfo=zone, fold=0).utcoffset() != dt.replace(tzinfo=zone, fold=1).utcoffset():
                raise ValueError("Ambiguous daylight-saving timestamp. Normalize the source to explicit offsets first.")
            dt = dt.replace(tzinfo=zone)
        return int(dt.timestamp() * 1000)

    players = []
    pfile = "qc_players.csv" if pubo else "player_ratings.csv"
    for row in files[pfile]:
        key = ladder(row)
        cfg = next(m for m in mappings if m["ladder"] == key)
        rating = integer(row, "rating", cfg.get("initial"))
        rd = integer(row, "deviation", cfg.get("deviation"))
        if rating is None or rd is None:
            raise ValueError("Unrated players need explicit initial/deviation defaults in their mapping.")
        players.append(
            dict(
                ladder=key,
                player=dict(
                    id=str(row["user_id"]),
                    name=row.get("nick") or str(row["user_id"]),
                    rating=rating,
                    deviation=rd,
                    wins=integer(row, "wins", 0),
                    losses=integer(row, "losses", 0),
                    draws=integer(row, "draws", 0),
                    streak=integer(row, "streak", 0),
                ),
                hidden=bool(integer(row, "is_hidden", 0)),
                lastRankedAt=timestamp(row.get("last_ranked_match_at")) if not pubo else None,
            )
        )
    matches = []
    changes = []
    if mode == "history":
        mfile = "qc_matches.csv" if pubo else "matches.csv"
        roster_file = "qc_player_matches.csv" if pubo else "match_players.csv"
        hfile = "qc_rating_history.csv" if pubo else "rating_history.csv"
        if any(name not in files for name in [mfile, roster_file, hfile]):
            raise ValueError("Full history requires matches, match players and rating history CSVs.")
        rosters = {}
        for row in files[roster_file]:
            mid = str(row["match_id"])
            team = integer(row, "team")
            if team not in (0, 1):
                raise ValueError(f"Match {mid} has a participant without a team.")
            teams = rosters.setdefault(mid, [[], []])
            teams[team].append(str(row["user_id"]))
        for row in files[mfile]:
            key = ladder(row)
            mid = str(row["match_id"])
            winner = integer(row, "winner_team" if pubo else "winner")
            at = timestamp(row.get("at" if pubo else "reported_at"))
            queue_key = str(row["queue"] if pubo else row["queue_id"])
            cfg = next(m for m in mappings if m["ladder"] == key)
            if queue_key not in cfg["queues"]:
                raise ValueError(f"Source queue {queue_key} requires an explicit queue mapping.")
            if pubo and winner is None and not null_result:
                raise ValueError(
                    "NULL PUBobot winners need an explicit --null-result draw or abort; the source does not distinguish them."
                )
            result = (
                ("alpha" if winner == 0 else "beta" if winner == 1 else null_result or "draw")
                if pubo or integer(row, "ranked", 0)
                else "abort"
            )
            if at is None:
                raise ValueError("A historical match is missing its report timestamp.")
            matches.append(
                dict(
                    sourceId=mid,
                    ladder=key,
                    queue=queue_key,
                    at=at,
                    endedAt=at,
                    result=result,
                    teams=rosters.get(mid, [[], []]),
                )
            )
        for n, row in enumerate(files[hfile]):
            key = ladder(row)
            before = integer(row, "rating_before")
            rd = integer(row, "deviation_before")
            if before is None or rd is None:
                raise ValueError("Historical rating/deviation baseline is missing.")
            match_id = integer(row, "match_id")
            changes.append(
                dict(
                    sourceId=str(row.get("id") or n),
                    ladder=key,
                    userId=str(row["user_id"]),
                    matchId=str(match_id) if match_id is not None else None,
                    at=timestamp(row["at"]),
                    before=before,
                    after=before + integer(row, "rating_change", 0),
                    deviationBefore=rd,
                    deviationAfter=rd + integer(row, "deviation_change", 0),
                    reason=row.get("reason") or "Imported legacy history",
                )
            )
        # PUBobot exports lack last-played dates; derive them only from explicitly classified ranked matches.
        if pubo:
            for row in players:
                row["lastRankedAt"] = max(
                    (
                        m["endedAt"]
                        for m in matches
                        if m["result"] != "abort" and row["player"]["id"] in sum(m["teams"], [])
                    ),
                    default=None,
                )
    if len(players) > 500 or len(matches) > 500 or len(changes) > 8000:
        raise ValueError(
            "Pilot import limits: 500 players, 500 matches, 8,000 history rows. Larger migrations require a reviewed batch plan."
        )
    return dict(
        version=1,
        source=source,
        sourceId=source_id,
        mode=mode,
        mappings=[{k: m[k] for k in ("ladder", "gameId", "engine", "queues") if k in m} for m in mappings],
        players=players,
        matches=matches,
        changes=changes,
        unsupported=[
            "gold and predictions",
            "quiz",
            "drafts",
            "game API achievements",
            "legacy channel settings",
            "external profile mappings",
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="CSV snapshot directory or PUBobot ZIP")
    parser.add_argument("--source", choices=["pubobot", "nammapubobot"], required=True)
    parser.add_argument("--mapping", required=True, help="Explicit ladder/channel/queue mapping JSON")
    parser.add_argument("--mode", choices=["seed", "history"], default="seed")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--timezone", help="Required for naive PUBobot wall times")
    parser.add_argument("--null-result", choices=["draw", "abort"])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        data = convert(
            read_files(args.input),
            args.source,
            json.loads(Path(args.mapping).read_text()),
            args.mode,
            args.source_id,
            args.timezone,
            args.null_result,
        )
        Path(args.output).write_text(json.dumps(data, indent=2) + "\n")
        print(
            f"Prepared {len(data['players'])} players, {len(data['matches'])} matches, {len(data['changes'])} history rows. Upload this JSON to the staging import preview; nothing has been applied."
        )
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        parser.exit(2, f"Cannot convert: {error}\n")


if __name__ == "__main__":
    main()
