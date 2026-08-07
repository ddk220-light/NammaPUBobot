# -*- coding: utf-8 -*-
"""Things built ON TOP of a pickup game, each self-contained.

Every feature here owns its own tables, its own job singleton if it needs a
tick, and its own commands. None of them is load-bearing: turn any one off and
the queues still fill, the matches still run and the ratings still move.

A feature may import runtime/ and pickup/. It may NOT be imported BY pickup/ —
the domain announces what happened and a feature subscribes in the composition
root (bot/wiring.py). Feature-to-feature imports are allowed but worth
resisting; where two of them already share something, that something usually
belongs a layer down.
"""
