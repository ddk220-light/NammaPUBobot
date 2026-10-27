#!/usr/bin/env python3
"""Convert a complete read-only legacy wallet/ledger CSV export for one explicit community.

This tool neither connects to a database nor mutates it. Preserve the complete original ledger.
"""

import argparse
import csv
import json
from pathlib import Path


def convert(directory, community, source, channels=None):
    def rows(name):
        path = Path(directory) / name
        if path.stat().st_size > 25_000_000:
            raise ValueError("Source CSV exceeds 25 MB; review a batch plan")
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))

    wallets = [r for r in rows("gold_balances.csv") if r["community_id"] == community]
    ledger = [r for r in rows("gold_ledger.csv") if r["community_id"] == community]
    ledger.sort(key=lambda r: (int(r["created_at"]), int(r["id"])))
    users = {r["user_id"] for r in wallets}
    if len(users) != len(wallets):
        raise ValueError("Duplicate source wallets")
    if len(wallets) > 5000 or len(ledger) > 50000:
        raise ValueError("Snapshot exceeds bounded import limits")
    if len({r["id"] for r in ledger}) != len(ledger):
        raise ValueError("Duplicate source ledger IDs")
    if any(r["user_id"] not in users for r in ledger):
        raise ValueError("Ledger rows require their source wallets")
    output = []
    for wallet in wallets:
        history = [r for r in ledger if r["user_id"] == wallet["user_id"]]
        if sum(int(r["amount"]) for r in history) != int(wallet["balance"]):
            raise ValueError("Source ledger and balance disagree; do not manufacture an opening adjustment")
        seeds = [int(r["created_at"]) for r in history if r["entry_type"] == "seed"]
        if not seeds:
            raise ValueError("Missing original seed timestamp")
        balance = 0
        for r in history:
            balance += int(r["amount"])
            if balance < 0:
                raise ValueError("Source ledger running balance is negative")
        output.append(dict(userId=wallet["user_id"], balance=int(wallet["balance"]), seededAt=min(seeds) * 1000))
    markets = []
    posts = Path(directory) / "prediction_posts.csv"
    if posts.exists():
        if not channels:
            raise ValueError("Specify original channel IDs to map prediction history to one game")
        bets = rows("prediction_bets.csv")
        for post in rows("prediction_posts.csv"):
            if post["channel_id"] not in channels:
                continue
            if post["status"] in ("open", "frozen"):
                raise ValueError("Resolve or refund live prediction books before migration")
            if post["status"] not in ("resolved", "void", "no_action"):
                raise ValueError("Unknown prediction book state")
            selected = [b for b in bets if b["post_id"] == post["id"]]
            if len({b["user_id"] for b in selected}) != len(selected):
                raise ValueError("Duplicate source bettors")
            if any(b["user_id"] not in users or b["side"] not in ("0", "1") or int(b["stake"]) <= 0 for b in selected):
                raise ValueError("Invalid source bettor or side")
            winner = post.get("winner_idx")
            if post["status"] == "resolved" and winner not in ("0", "1"):
                raise ValueError("Settled book has no winner")
            payment_kind = "payout" if post["status"] == "resolved" else "refund"
            paid = {
                b["user_id"]: sum(
                    int(entry["amount"])
                    for entry in ledger
                    if entry.get("post_id") == post["id"]
                    and entry["user_id"] == b["user_id"]
                    and entry["entry_type"] == payment_kind
                )
                for b in selected
            }
            pool = sum(int(b["stake"]) for b in selected)
            winning_pool = sum(int(b["stake"]) for b in selected if b["side"] == winner)
            for bet in selected:
                expected = (
                    int(bet["stake"])
                    if payment_kind == "refund"
                    else (pool * int(bet["stake"]) // winning_pool if bet["side"] == winner and winning_pool else 0)
                )
                if paid[bet["user_id"]] != expected:
                    raise ValueError("Prediction payout history does not reconcile")
            markets.append(
                dict(
                    id=post["id"],
                    matchSourceId=post.get("match_id") or None,
                    status="settled" if post["status"] == "resolved" else "refunded",
                    winner="alpha" if winner == "0" else "beta" if winner == "1" else None,
                    openedAt=int(post["opened_at"]) * 1000,
                    closedAt=int(post.get("resolved_at") or post["freezes_at"]) * 1000,
                    bets=[
                        dict(
                            userId=b["user_id"],
                            side="alpha" if b["side"] == "0" else "beta",
                            stake=int(b["stake"]),
                            payout=paid[b["user_id"]],
                        )
                        for b in selected
                    ],
                )
            )
    return dict(
        sourceId=source,
        wallets=output,
        markets=markets,
        ledger=[
            dict(
                id=r["id"],
                userId=r["user_id"],
                amount=int(r["amount"]),
                kind=r["entry_type"],
                at=int(r["created_at"]) * 1000,
            )
            for r in ledger
        ],
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory")
    p.add_argument("--community", required=True)
    p.add_argument("--source-id", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--channels", help="Comma-separated legacy channel IDs belonging to this one destination game")
    args = p.parse_args()
    try:
        bundle = convert(
            args.directory, args.community, args.source_id, set(args.channels.split(",")) if args.channels else None
        )
        Path(args.output).write_text(json.dumps(bundle, indent=2) + "\n")
        print(
            f"Validated {len(bundle['wallets'])} wallets and {len(bundle['ledger'])} ledger rows. Choose one destination game explicitly."
        )
    except (ValueError, KeyError, OSError) as error:
        p.exit(2, f"Cannot convert economy: {error}\n")
