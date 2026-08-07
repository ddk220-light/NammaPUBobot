# -*- coding: utf-8 -*-
"""Replay acquisition + extraction: the LIVE ingest path's only dependency
outside `bot/` and `core/`.

This package is the surviving half of the retired `utils/replay_quiz/`. That
directory mixed two unrelated things under one name: the offline quiz corpus
(SQLite build, question generation, weekly sampling -- all deleted along with
the offline player bank, now generated live by nammaoe2bot/features/quiz/player_bank.py) and the
replay parser the bot itself runs on every ingested match. Only the second is
here, and the name no longer claims it has anything to do with the quiz.

WHY IT IS NOT UNDER `bot/`. `extract_match` is a dependency-light library
(stdlib plus a lazy `mgz` import) with four importers: bot/replay_stats/parse.py
(live), utils/classifications/runner.py,
utils/classifications/pipeline/ingester.py and utils/backfill_strategy_tags.py
(offline). Importing anything under `bot/` executes bot/__init__.py, which boots
the whole Discord bot -- nextcord, config.cfg, the MySQL adapter. The offline
pipelines have neither; utils/validate_apm.py shows what importing
`bot.replay_stats.*` from a script actually costs (a 40-line fake-package and
fake-core shim). Three more copies of that shim, to relocate a parser that needs
none of it, would be the wrong trade. The rule `utils/` = "never imported by the
bot" is already untrue (bot/web.py and bot/replay_stats/classifications.py both
import utils/classifications/); the fix for that is a real shared-library
boundary, not the move of one file.

THE PARSER PIN. mgz must be the sanduckhan aoc-mgz fork -- stock PyPI mgz 1.8.51
cannot parse current (save_version 67.x) replays; mgz.model.parse_match dies in
the header `players` section. The pin lives in the repo's own requirements.txt
(the deploy image installs it); `mgz_save67.patch` beside this file is that fix
as a 2-line diff, for patching a stock mgz in a local .replay_scratch/ instead
-- which is what `PYTHONPATH=.replay_scratch` in the offline runners refers to.
"""
