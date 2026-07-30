"""The data registry is the single source of truth for table contracts.

Scans every ensure_table/FactoryTable declaration under bot/ and core/ and
asserts exact two-way agreement with core.data_registry.REGISTRY. A table
added without a registry entry (or an entry whose table was dropped) fails CI.
"""
import os
import re

from core.data_registry import REGISTRY

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DECL = re.compile(r"""(?:tname|name)\s*=\s*['"]([A-Za-z0-9_]+)['"]""")
_SCAN_HINTS = ("ensure_table", "FactoryTable")


def _declared_tables():
	found = set()
	for base in ("bot", "core"):
		for dirpath, _dirs, files in os.walk(os.path.join(_ROOT, base)):
			if "__pycache__" in dirpath:
				continue
			for f in files:
				if not f.endswith(".py"):
					continue
				src = open(os.path.join(dirpath, f), encoding="utf-8").read()  # noqa: SIM115
				if not any(h in src for h in _SCAN_HINTS):
					continue
				# Only lines inside declaration blocks matter; tname= is unique
				# to them, and FactoryTable(name=...) is caught by the same rx.
				for line in src.splitlines():
					if "tname=" in line or "FactoryTable(name" in line:
						m = _DECL.search(line)
						if m and m.group(1) not in ("None",):
							found.add(m.group(1))
	return found


def test_every_declared_table_is_registered_and_vice_versa():
	declared = _declared_tables()
	declared.discard("schema_migrations")  # runner-owned, created raw
	registered = set(REGISTRY)
	assert declared - registered == set(), f"declared but unregistered: {sorted(declared - registered)}"
	assert registered - declared == set(), f"registered but undeclared: {sorted(registered - declared)}"


def test_registry_entries_are_complete():
	for name, meta in REGISTRY.items():
		assert meta.get("layer") in ("core", "raw", "link", "derived", "ops"), name
		assert meta.get("tenancy") in ("global", "community", "channel"), name
		assert meta.get("writer"), name
		assert meta.get("retention") in ("forever", "sweepable"), name
