# -*- coding: utf-8 -*-
"""Live replay -> cls_* sync.

Replay parsing already writes the rich rs_* tables. This module runs the same strategy
classifier used by the offline pipeline and stores per-match cls_* rows so web tags and
player commentary refresh as new matches are ingested.
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
        await dbw.insert_many("cls_results", result_rows, on_dublicate="replace")
    if metric_rows:
        await dbw.insert_many("cls_result_metrics", metric_rows, on_dublicate="replace")


async def sync_match(extracted, played_at_epoch, db_adapter=None):
    aoe2_match_id = extracted["match"]["aoe2_match_id"]
    result_rows, metric_rows = classification_rows(extracted, aoe2_match_id, played_at_epoch)
    await write_classification_rows(aoe2_match_id, result_rows, metric_rows, db_adapter=db_adapter)
    return len(result_rows), len(metric_rows)
