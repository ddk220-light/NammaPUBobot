#!/usr/bin/env python3
"""
Startup wrapper for Railway deployment.
Generates config.cfg from environment variables, then launches the bot.
"""
import os
import sys
import subprocess

TEMPLATE = '''# -*- coding: utf-8 -*-
# Auto-generated from environment variables for Railway deployment

DC_BOT_TOKEN = "{dc_bot_token}"
DC_CLIENT_ID = {dc_client_id}
DC_CLIENT_SECRET = "{dc_client_secret}"
DC_INVITE_LINK = "{dc_invite_link}"
DC_OWNER_ID = {dc_owner_id}
DC_SLASH_SERVERS = [{dc_slash_servers}]
FLAGSHIP_GUILD_IDS = [{flagship_guild_ids}]

PUBOBOT_USER_ID = {pubobot_user_id}
LOBBYBOT_USER_ID = {lobbybot_user_id}
DEPLOYMENT_MODE = "{deployment_mode}"
REPLAY_INGEST_ENABLED = "{replay_ingest_enabled}"

DB_URI = "{db_uri}"
LOG_LEVEL = "{log_level}"
STATUS = "{status}"

WS_ENABLE = {ws_enable}
WS_HOST = "0.0.0.0"
WS_PORT = {ws_port}
WS_ROOT_URL = "{ws_root_url}"
'''


# What nammaoe2bot/runtime/config.py's _coerce() accepts as True for a bool key — the sole
# authority on what a config string means. Mirrored here rather than imported
# because importing nammaoe2bot.runtime.config would EXECUTE it, and it loads config.cfg: the
# very file this script has not written yet. tests/test_migrations.py's
# test_the_replay_ingest_switch_resolves_the_same_way_in_both_config_paths pins
# the two literals together so they cannot drift apart.
_TRUE = ('1', 'true', 'yes', 'on')


def build_db_uri():
    """Build DB_URI from Railway's MySQL plugin variables or explicit DB_URI."""
    if os.environ.get("DB_URI"):
        return os.environ["DB_URI"]

    # Railway MySQL plugin provides these variables
    host = os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST", "")
    port = os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT", "3306")
    user = os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER", "")
    password = os.environ.get("MYSQLPASSWORD") or os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("MYSQLDATABASE") or os.environ.get("MYSQL_DATABASE", "")

    if host and user and database:
        return f"mysql://{user}:{password}@{host}:{port}/{database}"

    return ""


def main():
    db_uri = build_db_uri()
    if not db_uri:
        print("ERROR: No database configured. Set DB_URI or add a MySQL service in Railway.")
        sys.exit(1)

    token = os.environ.get("DC_BOT_TOKEN", "")
    if not token:
        print("ERROR: DC_BOT_TOKEN environment variable is required.")
        sys.exit(1)

    owner_id = os.environ.get("DC_OWNER_ID", "0")
    if owner_id == "0":
        print("WARNING: DC_OWNER_ID not set. Bot owner commands won't work.")

    # Fail-closed on these two: they're used by nammaoe2bot/discord/events.py on_message
    # to gate ELO sync and civ sync. Silently defaulting to hardcoded
    # Discord user IDs (as we used to) means a misconfigured deployment
    # would either attribute every random bot's messages to Pubobot or
    # do nothing at all and look healthy. Better to refuse to start.
    pubobot_user_id = os.environ.get("PUBOBOT_USER_ID", "")
    if not pubobot_user_id:
        print("ERROR: PUBOBOT_USER_ID environment variable is required.")
        print("       Set it to the Discord user ID of the Pubobot bot whose")
        print("       ELO result messages NammaAoe2Bot should mirror.")
        sys.exit(1)
    lobbybot_user_id = os.environ.get("LOBBYBOT_USER_ID", "")
    if not lobbybot_user_id:
        print("ERROR: LOBBYBOT_USER_ID environment variable is required.")
        print("       Set it to the Discord user ID of the AOE2LobbyBOT whose")
        print("       match embeds NammaAoe2Bot should scrape for civ data.")
        sys.exit(1)

    # Resolved once, here, so the value written into config.cfg and the value
    # reported below are provably the same string.
    deployment_mode = os.environ.get("DEPLOYMENT_MODE", "self_hosted")
    replay_ingest_enabled = os.environ.get("REPLAY_INGEST_ENABLED", "True")

    config_content = TEMPLATE.format(
        dc_bot_token=token,
        dc_client_id=os.environ.get("DC_CLIENT_ID", "0"),
        dc_client_secret=os.environ.get("DC_CLIENT_SECRET", ""),
        dc_invite_link=os.environ.get("DC_INVITE_LINK", ""),
        dc_owner_id=owner_id,
        dc_slash_servers=os.environ.get("DC_SLASH_SERVERS", ""),
        flagship_guild_ids=os.environ.get("FLAGSHIP_GUILD_IDS", ""),
        pubobot_user_id=pubobot_user_id,
        lobbybot_user_id=lobbybot_user_id,
        deployment_mode=deployment_mode,
        # Defaults to True: 007_raw_renames dropped the single-row ops table whose
        # one row had this switch ON in production, so an unset env var must keep
        # ingestion running rather than silently stopping it.
        #
        # Emitted QUOTED, unlike the older {ws_enable} above it. config.cfg is
        # loaded as Python source, so an unquoted `REPLAY_INGEST_ENABLED=false`
        # (a perfectly ordinary thing to type into Railway) would render as the
        # bare name `false` and raise NameError at import — taking the whole boot
        # down over a config value. Quoted, it is always a valid string literal
        # and nammaoe2bot/runtime/config.py's bool coercion ('1'/'true'/'yes'/'on', anything
        # else False) decides what it means.
        replay_ingest_enabled=replay_ingest_enabled,
        db_uri=db_uri,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        status=os.environ.get("STATUS", "NammaAoe2Bot"),
        ws_enable=os.environ.get("WS_ENABLE", "False"),
        ws_port=os.environ.get("WS_PORT", os.environ.get("PORT", "8080")),
        ws_root_url=os.environ.get("WS_ROOT_URL", ""),
    )

    with open("config.cfg", "w") as f:
        f.write(config_content)

    print("config.cfg generated from environment variables.")
    print(f"Database: {db_uri.split('@')[-1] if '@' in db_uri else '(configured)'}")

    # Report the RESOLVED replay-ingest switch, always, both ways. A Railway
    # variable that EXISTS but is empty yields "" from the get() above — not the
    # "True" default, which only applies when the name is absent entirely — and
    # "" coerces to False. Ingestion would then stop for the whole deployment
    # with nothing anywhere saying why; this line is that "why". The raw value is
    # printed with !r specifically so the empty case shows up as '' rather than
    # as blank space, and `unset` distinguishes the defaulted case from a var
    # deliberately set to `True`.
    _origin = "" if "REPLAY_INGEST_ENABLED" in os.environ else ", unset - defaulted"
    _on = replay_ingest_enabled.strip().lower() in _TRUE
    _known_mode = deployment_mode.strip().lower().replace("-", "_")
    _mode = _known_mode if _known_mode in ("hosted", "self_hosted") else "hosted"
    _effective = _on and _mode == "self_hosted"
    print(f"Deployment mode: {_mode} (DEPLOYMENT_MODE={deployment_mode!r})")
    print(f"Replay ingest: {'ENABLED' if _effective else 'DISABLED'} "
          f"(REPLAY_INGEST_ENABLED={replay_ingest_enabled!r}{_origin})")

    # Launch the bot
    # `-m`, not the file path: running nammaoe2bot/__main__.py directly puts
    # that DIRECTORY on sys.path instead of the repo root, so every
    # `import nammaoe2bot.x` fails. -m imports the package properly.
    os.execvp(sys.executable, [sys.executable, "-m", "nammaoe2bot"])


if __name__ == "__main__":
    main()
