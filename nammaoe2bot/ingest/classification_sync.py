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
nammaoe2bot/derived/backfill.py reconciles game_labels against cls_results on every pass, and
nammaoe2bot/ingest/classifications.py's write_extracted_match -- called from
store.write_match on the same ingest, strictly before this runs -- is what keeps that
source populated for a freshly ingested match. Deleting that writer without moving the
backfill off cls_results would make every new match's cls_results set EMPTY while its
game_labels set is not, i.e. permanently pending. That used to mean the reconciler
"healed" the match by deleting the labels this module had just written; it now refuses
to act on an empty source it cannot verify (backfill._source_is_trustworthy) and
quarantines the match loudly instead, so the failure costs reconciliation rather than
data. That backstop is not a licence to delete the writer -- every new match would sit
quarantined and unreconciled. tests/test_replay_stats_store.py's
test_the_ingest_path_still_writes_cls_results_for_every_match pins the call, because
three prose warnings including this one did not. Stage 6 retires the two together.
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

    Raises rather than swallowing: nammaoe2bot/ingest/jobs.py's ingest already wraps this
    call, logs the failure against the match id, and carries on to mark the ingest done --
    and nammaoe2bot/derived/backfill.py writes the match's labels from its cls_results within
    POLL_INTERVAL. A second guard here could only turn a failure into a silent "0 labels"
    success line.

    That recovery is one-directional, and the earlier claim here that the backfill
    "rewrites the match either way" was false in the direction that mattered. It repairs
    THIS module failing, because the source (cls_results, written moments earlier by
    store.write_match) still describes the match while game_labels does not. It cannot
    repair the SOURCE failing: an empty cls_results beside a full game_labels used to make
    the reconciler delete the labels and report convergence. backfill.py now refuses that
    write unless cls_match_ingest certifies the classifier completed with zero results.

    `db_adapter` is threaded through rather than dropped: a caller that passes one is
    writing this whole match through that specific connection, and letting the derived
    write fall back to its module-global `db` would send it to a different database.

    game_labels is imported inside the function, not at module scope: jobs.py imports this
    module from inside a running coroutine, and nammaoe2bot/derived/__init__.py's ensure_table
    calls drive the loop with run_until_complete (see that package's docstring).
    """
    from nammaoe2bot.derived import game_labels

    aoe2_match_id = extracted["match"]["aoe2_match_id"]
    result_rows, metric_rows = classification_rows(extracted, aoe2_match_id, played_at_epoch)
    rows = game_labels.label_rows(result_rows, metric_rows, played_at_epoch)
    await game_labels.write(aoe2_match_id, rows, db_adapter=db_adapter)
    return len(rows)
