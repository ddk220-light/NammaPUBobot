# -*- coding: utf-8 -*-
"""Process concerns: configuration, logging, the Discord client, the database
adapter, schema migrations, on-disk paths and the table registry.

The bottom layer. "Bottom" here means BELOW THE DOMAIN, not free of I/O:
database/ knows MySQL and client.py knows Discord, and both are the same kind
of thing — one connection to the outside world, constructed once at import and
used by every layer above. What none of it knows is what a pickup game is.

Nothing here may import from pickup/, features/, discord/ or web/. The
dependency runs one way, and tests/test_import_cycles.py is what keeps it
honest.
"""
