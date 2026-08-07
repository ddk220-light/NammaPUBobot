# -*- coding: utf-8 -*-
"""Where things are on disk, answered once.

Eight modules used to compute the repo root themselves, each by counting `..`
from its own `__file__`:

	_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

That is a correct answer to "where am I" and the wrong way to ask it. The count
encodes how deep the module happens to sit, so moving a file one level changes
what it points at — silently, because a wrong directory does not raise. It
resolves, it is simply empty, and the caller reads that as "no data shipped in
this image". The migrations post-condition that guards a restored-backup
disaster does exactly that: `_seed_csv_rows_available()` returns 0 for anything
it cannot read, and 0 means "nothing to check", so a path off by one level
disarms it and the boot proceeds.

Anchored on the PACKAGE instead. Every module inside nammaoe2bot is the same
distance from nammaoe2bot/, which is zero — the package knows its own location
regardless of how deeply the importer sits inside it.

A path that is genuinely relative to one module — web/server.py's page.html
sits beside it and moves with it — should stay `os.path.dirname(__file__)` and
does not belong here.
"""
import os

import nammaoe2bot

#: nammaoe2bot/ itself.
PACKAGE_ROOT = os.path.dirname(os.path.abspath(nammaoe2bot.__file__))

#: The repository (and, in the Docker image, the working directory).
REPO_ROOT = os.path.dirname(PACKAGE_ROOT)

#: Committed data files: seed CSVs, the quiz schedule, the replay cache.
DATA_DIR = os.path.join(REPO_ROOT, "data")


def data(*parts):
	"""A path inside data/. `data("quiz_schedule.json")`."""
	return os.path.join(DATA_DIR, *parts)
