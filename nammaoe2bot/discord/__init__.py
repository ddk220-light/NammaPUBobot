# -*- coding: utf-8 -*-
"""The Discord adapter: everything that knows Discord's shapes.

`slash.py` registers the 44 slash commands and is the single readable index of
the surface; `groups.py` declares the admin subcommand groups (every one of
them carries `_admin_kwargs`, or its whole subtree reappears in every player's
menu); `autocomplete.py` feeds them; `context.py`, `slash_context.py` and
`message_context.py` are the three ways a command reaches its channel;
`events.py` holds the client event handlers and the one-second tick.

`commands/` holds the handlers for the pickup game itself — queues, matches,
config, moderation, stats. A FEATURE'S commands live with the feature
(features/quiz/commands.py, features/betting/commands.py,
features/identity/commands.py) and slash.py imports them by name, so adding a
quiz command touches the quiz package and one registration line.

This layer is reached FROM Discord and reaches DOWN into features and the
domain. Nothing below it imports this package.
"""
