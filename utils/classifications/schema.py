"""Raw CREATE TABLE IF NOT EXISTS for the cls_* tables, used by the offline runner (which
connects via aiomysql, not the bot adapter). The bot mirrors these exact columns via
db.ensure_table in bot/classifications/__init__.py — keep the two in sync."""

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
        INDEX cls_results_profile (`key`, profile_id)
    )""",
    """CREATE TABLE IF NOT EXISTS cls_result_metrics (
        `key` VARCHAR(191) NOT NULL,
        aoe2_match_id BIGINT NOT NULL,
        player_number BIGINT NOT NULL,
        metric VARCHAR(191) NOT NULL,
        value FLOAT,
        PRIMARY KEY (`key`, aoe2_match_id, player_number, metric),
        INDEX cls_metrics_metric (`key`, metric)
    )""",
]
