# -*- coding: utf-8 -*-
"""Which civilisation each player picked, and what that is worth.

`pools.py` builds the randomised civ pools and reads the per-civ win rates
behind them. `sync.py` and `matcher.py` are the two ways a match's civs are
learned — from AOE2LobbyBOT's completed-match embed, and by matching a finished
pickup against the aoe2companion API — and `reconcile.py` is the tick that
retries the ones neither caught first time.
"""
