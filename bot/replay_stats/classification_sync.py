# -*- coding: utf-8 -*-
"""Live replay -> game_labels sync.

Replay parsing already writes the rich replay_* tables. This module runs the same strategy
classifier the offline pipeline uses and stores what it produced as derived-global
game_labels rows, so a card or /insights reads one stored fact per player per game instead
of re-deriving it at render time.

STAGE 5c RETIRED THE cls_* HALF OF THIS MODULE. It used to write cls_results and
cls_result_metrics here as well -- a deliberate dual-write, kept while /insights and the
match cards still read them. They read game_labels now, so the write is gone.

That does NOT make cls_results dead, and nothing here should treat it as such:
bot/derived/backfill.py reconciles game_labels against cls_results on every pass, and
bot/replay_stats/classifications.py's write_extracted_match -- called from
store.write_match on the same ingest, strictly before this runs -- is what keeps that
source populated for a freshly ingested match. Deleting that writer without moving the
backfill off cls_results would make every new match's cls_results set EMPTY while its
game_labels set is not, i.e. permanently pending, and the reconciler would then "heal" it
by deleting the labels this module just wrote. Stage 6 retires the two together.
"""


def classification_rows(extracted, aoe2_match_id, played_at_epoch):
    from utils.classifications.pipeline.classify import classify_game

    result_rows, metric_rows, _player_rows = classify_game(
        extracted, int(aoe2_match_id), int(played_at_epoch or 0)
    )
    return result_rows, metric_rows


async def sync_match(extracted, played_at_epoch, db_adapter=None):
    """Classify one freshly-parsed match and store its game_labels rows.

    Returns the number of label rows written -- which is NOT the number of triggers that
    fired: label_rows drops every key outside game_labels' two allowlists (luck_baseline
    above all). The caller logs it, so it has to be the count of what was stored.

    Raises rather than swallowing: bot/replay_stats/jobs.py's ingest already wraps this
    call, logs the failure against the match id, and carries on to mark the ingest done --
    and bot/derived/backfill.py rewrites the match within POLL_INTERVAL either way. A
    second guard here could only turn a failure into a silent "0 labels" success line.

    `db_adapter` is threaded through rather than dropped: a caller that passes one is
    writing this whole match through that specific connection, and letting the derived
    write fall back to its module-global `db` would send it to a different database.

    game_labels is imported inside the function, not at module scope: jobs.py imports this
    module from inside a running coroutine, and bot/derived/__init__.py's ensure_table
    calls drive the loop with run_until_complete (see that package's docstring).
    """
    from bot.derived import game_labels

    aoe2_match_id = extracted["match"]["aoe2_match_id"]
    result_rows, metric_rows = classification_rows(extracted, aoe2_match_id, played_at_epoch)
    rows = game_labels.label_rows(result_rows, metric_rows, played_at_epoch)
    await game_labels.write(aoe2_match_id, rows, db_adapter=db_adapter)
    return len(rows)
