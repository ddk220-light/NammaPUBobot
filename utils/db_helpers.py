#!/usr/bin/env python3
"""Shared database connection helpers for utility scripts."""

import os
import sys
from importlib.machinery import SourceFileLoader

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def load_config():
    """Load config.cfg and return the config module."""
    try:
        return SourceFileLoader('cfg', os.path.join(PROJECT_ROOT, 'config.cfg')).load_module()
    except Exception:
        print("Error: Could not load config.cfg. Copy config.example.cfg and fill in DB_URI.",
              file=sys.stderr)
        return None


def parse_db_uri(db_uri):
    """Parse DB_URI string into connection kwargs for aiomysql.create_pool().

    Accepts: mysql://user:password@hostname:port/database
    Returns: dict with host, user, password, db, port keys.
    """
    uri = db_uri
    for prefix in ('mysql://', 'mysql+aiomysql://'):
        if uri.startswith(prefix):
            uri = uri[len(prefix):]
            break

    user, rest = uri.split(':', 1)
    password, rest = rest.split('@', 1)
    host_part, db_name = rest.split('/', 1)
    if ':' in host_part:
        host, port = host_part.split(':')
        port = int(port)
    else:
        host = host_part
        port = 3306

    return dict(host=host, user=user, password=password, db=db_name, port=port)


async def create_pool(db_uri=None):
    """Create and return an aiomysql connection pool.

    If db_uri is None, loads it from config.cfg.
    Returns pool or None on failure.
    """
    import aiomysql

    if db_uri is None:
        cfg = load_config()
        if cfg is None:
            return None
        db_uri = getattr(cfg, 'DB_URI', '')
        if not db_uri:
            print("Error: DB_URI not set in config.cfg", file=sys.stderr)
            return None

    conn_kwargs = parse_db_uri(db_uri)
    return await aiomysql.create_pool(
        **conn_kwargs, charset='utf8mb4', autocommit=True,
        cursorclass=aiomysql.cursors.DictCursor
    )
