# -*- coding: utf-8 -*-
"""NammaAoe2Bot — a Discord bot for organising AoE2 pickup games.

VERSION ONLY. No state, no re-exports, no side effects. The last package root
that held either is the reason this restructure exists: see bot/__init__.py's
docstring for what a re-export shelf cost, and bot/app.py for where the state
went.

Boot wiring — the imports that exist for a side effect, and the subscription of
features to the match lifecycle — lives in bootstrap.py and wiring.py, called
explicitly by the entrypoint.
"""
__version__ = "2.0.0"
