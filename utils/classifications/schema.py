"""Raw CREATE TABLE IF NOT EXISTS for the cls_* tables, used by the offline runner (which
connects via aiomysql, not the bot adapter). The bot mirrors these exact columns via
db.ensure_table in nammaoe2bot/derived/classifications/__init__.py — keep the two in sync."""

CLS_TABLES = [
    """CREATE TABLE IF NOT EXISTS cls_classifications (
        `key` VARCHAR(191) NOT NULL,
        title VARCHAR(191),
        description VARCHAR(2000),
        trigger_spec VARCHAR(2000),
        version BIGINT,
        status VARCHAR(191),
        updated_at BIGINT,
        PRIMARY KEY (`key`)
    )""",
    """CREATE TABLE IF NOT EXISTS cls_data_requirements (
        `key` VARCHAR(191) NOT NULL,
        `field` VARCHAR(191) NOT NULL,
        source VARCHAR(191),
        status VARCHAR(191),
        note VARCHAR(2000),
        PRIMARY KEY (`key`, `field`)
    )""",
    """CREATE TABLE IF NOT EXISTS cls_results (
        `key` VARCHAR(191) NOT NULL,
        aoe2_match_id BIGINT NOT NULL,
        player_number BIGINT NOT NULL,
        profile_id BIGINT,
        identity VARCHAR(191),
        civ VARCHAR(191),
        team VARCHAR(191),
        winner TINYINT(1),
        played_at BIGINT,
        PRIMARY KEY (`key`, aoe2_match_id, player_number),
        INDEX cls_results_window (`key`, played_at),
        INDEX cls_results_profile (`key`, profile_id),
        -- Per-match lookup for nammaoe2bot/derived/backfill.py. The PK leads with `key`,
        -- so it cannot serve a match-only WHERE. Mirrored by the ensure_table
        -- declaration in nammaoe2bot/derived/classifications/__init__.py and by migration
        -- 006_derived_indexes (this DDL only ever runs on a table that does not
        -- exist yet, so it cannot add the index to the live database).
        INDEX cls_results_match (aoe2_match_id)
    )""",
    """CREATE TABLE IF NOT EXISTS cls_result_metrics (
        `key` VARCHAR(191) NOT NULL,
        aoe2_match_id BIGINT NOT NULL,
        player_number BIGINT NOT NULL,
        metric VARCHAR(191) NOT NULL,
        value FLOAT,
        PRIMARY KEY (`key`, aoe2_match_id, player_number, metric),
        INDEX cls_metrics_metric (`key`, metric),
        INDEX cls_result_metrics_match (aoe2_match_id)
    )""",
    # Per-player corpus totals (ALL scanned player-games, categorized or not). The denominator
    # for "% of total games" and the source of the "mixed / uncategorized" remainder on the web.
    """CREATE TABLE IF NOT EXISTS cls_player_totals (
        identity VARCHAR(191) NOT NULL,
        games BIGINT,
        wins BIGINT,
        losses BIGINT,
        PRIMARY KEY (identity)
    )""",
    """CREATE TABLE IF NOT EXISTS cls_match_ingest (
        aoe2_match_id BIGINT NOT NULL,
        classified_at BIGINT,
        result_rows BIGINT,
        status VARCHAR(191),
        PRIMARY KEY (aoe2_match_id)
    )""",
]
