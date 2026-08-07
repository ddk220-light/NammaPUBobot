# -*- coding: utf-8 -*-
"""Process concerns: configuration, logging, the database adapter, schema
migrations and the table registry.

The bottom layer. Nothing here knows what a pickup game is, and nothing here
may import from pickup/, features/, discord/ or web/ — the dependency runs
one way, and tests/test_import_cycles.py is what keeps it honest.
"""
