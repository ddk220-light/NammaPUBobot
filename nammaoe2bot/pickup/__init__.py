# -*- coding: utf-8 -*-
"""THE DOMAIN: the pickup game itself.

A channel holds queues, a queue fills and starts a match, a match checks in,
plays, and reports a result that moves ratings. That is the whole of it, and it
is the layer everything else is built on.

It may import runtime/ and nothing else. In particular it must not import a
feature — betting, the quiz, the lobby watcher, the storylines. A match
announces what happened (see match/events.py) and the features subscribe in
the composition root; tests/test_match_lifecycle.py checks the direction, and
tests/test_import_cycles.py checks that nothing has closed a loop.
"""
