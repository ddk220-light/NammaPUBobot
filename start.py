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

DB_URI = "{db_uri}"
LOG_LEVEL = "{log_level}"
COMMANDS_URL = "{commands_url}"
HELP = """{help_text}"""
STATUS = "{status}"

WS_ENABLE = {ws_enable}
WS_HOST = "0.0.0.0"
WS_PORT = {ws_port}
WS_ROOT_URL = "{ws_root_url}"
'''


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

    config_content = TEMPLATE.format(
        dc_bot_token=token,
        dc_client_id=os.environ.get("DC_CLIENT_ID", "0"),
        dc_client_secret=os.environ.get("DC_CLIENT_SECRET", ""),
        dc_invite_link=os.environ.get("DC_INVITE_LINK", ""),
        dc_owner_id=owner_id,
        dc_slash_servers=os.environ.get("DC_SLASH_SERVERS", ""),
        db_uri=db_uri,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        commands_url=os.environ.get("COMMANDS_URL",
            "https://github.com/Leshaka/PUBobot2/blob/main/COMMANDS.md#avaible-commands"),
        help_text=os.environ.get("HELP",
            "PUBobot2 is a discord bot for pickup games organisation."),
        status=os.environ.get("STATUS", "PUBobot2"),
        ws_enable=os.environ.get("WS_ENABLE", "False"),
        ws_port=os.environ.get("WS_PORT", os.environ.get("PORT", "8080")),
        ws_root_url=os.environ.get("WS_ROOT_URL", ""),
    )

    with open("config.cfg", "w") as f:
        f.write(config_content)

    print("config.cfg generated from environment variables.")
    print(f"Database: {db_uri.split('@')[-1] if '@' in db_uri else '(configured)'}")

    # Launch the bot
    os.execvp(sys.executable, [sys.executable, "PUBobot2.py"])


if __name__ == "__main__":
    main()
