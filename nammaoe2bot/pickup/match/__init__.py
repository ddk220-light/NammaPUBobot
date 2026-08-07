# -*- coding: utf-8 -*-
"""One match, from teams-formed to result-reported.

`match.py` holds the Match itself and its state machine; `events.py` is the
lifecycle it announces on; `checkin.py`, `substitution.py` and `embeds.py` are
the three things it delegates to.

`substitution.py` was `draft.py`, and the class inside it is still called
Draft. There is no draft any more — the two captain-pick commands went in the
command consolidation and `pick_teams` no longer offers the mode — so what the
class actually manages is /sub, /subfor and /subauto. The file says so; the
class keeps its name because renaming it is a separate change with its own
call sites.
"""
