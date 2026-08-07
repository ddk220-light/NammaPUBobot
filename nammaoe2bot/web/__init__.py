# -*- coding: utf-8 -*-
"""The public dashboard and the stats API.

`server.py` is an aiohttp app: Discord OAuth2 login, session management, the
config CRUD the authenticated dashboard uses, and the public read APIs.
`page.html` is the whole front end — one self-contained file, inline CSS and
JS, served by the handler above it.

It owns web_sessions and web_oauth_states and DERIVES NOTHING. Every stats
read goes to the derived layer; a figure computed here would be a second
implementation of something derived/ already answers, and the two would drift.
"""
