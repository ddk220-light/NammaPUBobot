# -*- coding: utf-8 -*-
"""Who is this person — one Discord user, one AoE2 profile, one answer.

`resolver.py` is the single store and the single read path; `solver.py` is the
half that PROPOSES links from evidence, which an admin then confirms. Identity
is the join key for nearly every table in the bot, which is why it is one
module and not the five it used to be.
"""
