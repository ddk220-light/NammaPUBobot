# -*- coding: utf-8 -*-
from asyncio import get_event_loop  # noqa: F401

from nammaoe2bot.runtime.config import cfg

from .mysql import Adapter as MySQLAdapter

# Scheme -> adapter, written out rather than assembled into a module path and
# handed to import_module. The old form was `import_module('core.DBAdapters.' +
# db_type)`, and when core/ moved into this package that string moved with
# nothing: it is not an import statement, so ruff does not see it, the static
# import-graph test does not see it, and no unit test dials a database. It
# would have failed at boot, on the first line of the first deploy.
#
# A real map is also the honest description: there has only ever been one
# adapter, and adding a second is a deliberate registration here rather than a
# file appearing in a directory.
ADAPTERS = {
	"mysql": MySQLAdapter,
}


def init_db(db_uri):
	db_type, db_address = db_uri.split("://", 1)
	if db_type not in ADAPTERS:
		raise ValueError(
			f"No database adapter for '{db_type}://' (DB_URI). Known: {sorted(ADAPTERS)}")
	return ADAPTERS[db_type](db_address)


db = init_db(cfg.DB_URI)
