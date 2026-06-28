# -*- coding: utf-8 -*-
"""Stored player commentary read-side.

The generation path is offline/local-first. The bot only declares and reads the
approved rows so web requests never call an LLM.
"""
from core.database import db

COMMENTARY_VERSION = "player-commentary-v1"

db.ensure_table(dict(
	tname="bot_player_commentary",
	columns=[
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="period", ctype=db.types.str),
		dict(cname="prompt_version", ctype=db.types.str),
		dict(cname="source_hash", ctype=db.types.str, notnull=False),
		dict(cname="generated_at", ctype=db.types.int, notnull=False),
		dict(cname="model", ctype=db.types.str, notnull=False),
		dict(cname="status", ctype=db.types.str, notnull=False),
		dict(cname="commentary_json", ctype=db.types.dict, notnull=False),
		dict(cname="facts_json", ctype=db.types.dict, notnull=False),
		dict(cname="error_text", ctype=db.types.text, notnull=False),
	],
	primary_keys=["user_id", "period", "prompt_version"],
))
