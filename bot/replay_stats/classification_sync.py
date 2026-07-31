# -*- coding: utf-8 -*-
"""Live replay -> cls_* sync.

Replay parsing already writes the rich rs_* tables. This module runs the same strategy
classifier used by the offline pipeline and stores per-match cls_* rows so web tags
refresh as new matches are ingested.
"""
from core.database import db


def classification_rows(extracted, aoe2_match_id, played_at_epoch):
    from utils.classifications.pipeline.classify import classify_game

    result_rows, metric_rows, _player_rows = classify_game(
        extracted, int(aoe2_match_id), int(played_at_epoch or 0)
    )
    return result_rows, metric_rows


async def write_classification_rows(aoe2_match_id, result_rows, metric_rows, db_adapter=None):
    dbw = db_adapter or db
    await dbw.execute("DELETE FROM cls_result_metrics WHERE aoe2_match_id=%s", [aoe2_match_id])
    await dbw.execute("DELETE FROM cls_results WHERE aoe2_match_id=%s", [aoe2_match_id])
    if result_rows:
        await dbw.insert_many("cls_results", result_rows, on_duplicate="replace")
    if metric_rows:
        await dbw.insert_many("cls_result_metrics", metric_rows, on_duplicate="replace")


async def sync_match(extracted, played_at_epoch, db_adapter=None):
    aoe2_match_id = extracted["match"]["aoe2_match_id"]
    result_rows, metric_rows = classification_rows(extracted, aoe2_match_id, played_at_epoch)
    await write_classification_rows(aoe2_match_id, result_rows, metric_rows, db_adapter=db_adapter)
    # cls_* above is the dual-write /insights and the web still read until
    # stage 5c -- this is ADDING a write, not replacing it. Best-effort: a
    # bug in the derived-layer mapping must never cost the cls_* write that
    # already succeeded above.
    #
    # db_adapter is threaded through, not dropped: a caller that passes one is
    # writing this whole match through that specific adapter, and letting
    # game_labels fall back to its module-global `db` would split one match's
    # writes across two different databases -- the cls_* rows in the caller's,
    # the derived rows in the bot's.
    try:
        from bot.derived import game_labels
        rows = game_labels.label_rows(result_rows, metric_rows, played_at_epoch)
        await game_labels.write(aoe2_match_id, rows, db_adapter=db_adapter)
    except Exception as e:
        from core.console import log
        log.error(f"game_labels write failed ({aoe2_match_id}): {e}")
    return len(result_rows), len(metric_rows)
