"""The DDL core/DBAdapters/mysql.py generates from an ensure_table declaration.

Only the schema-rendering half of the adapter is exercised — create_table builds
one CREATE TABLE string and hands it to execute(), so a fake execute is enough
and no MySQL is involved. The point is the `indexes=` declaration added for
bot/classifications/__init__.py: on a FRESH install the tables cls_results and
cls_result_metrics are created here rather than by migration 006_derived_indexes
(which runs before `import bot` and finds nothing to alter), so if create_table
silently dropped the declaration those installs would get no per-match index at
all and bot/derived/backfill.py would full-scan forever, with nothing anywhere
saying so.

Both DB drivers are stubbed unconditionally: CI installs pytest ONLY (see
.github/workflows/ci.yml), so importing the adapter for real is not an option,
and stubbing conditionally would mean the test exercised different code on a
developer machine than in CI.
"""
import asyncio
import sys
import types

import pytest


@pytest.fixture
def adapter_module(monkeypatch):
	fake_aiomysql = types.ModuleType("aiomysql")
	fake_aiomysql.Pool = object
	fake_aiomysql.cursors = types.SimpleNamespace(DictCursor=object)
	fake_aiomysql.create_pool = None
	monkeypatch.setitem(sys.modules, "aiomysql", fake_aiomysql)

	fake_pymysql = types.ModuleType("pymysql")
	fake_pymysql.err = types.SimpleNamespace(
		Error=Exception, InternalError=Exception, OperationalError=Exception, DataError=Exception)
	monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
	monkeypatch.setitem(sys.modules, "pymysql.err", fake_pymysql.err)

	monkeypatch.delitem(sys.modules, "core.DBAdapters.mysql", raising=False)
	import core.DBAdapters.mysql as mysql
	monkeypatch.delitem(sys.modules, "core.DBAdapters.mysql", raising=False)
	return mysql


def _ddl(adapter_module, table):
	"""The single CREATE TABLE create_table() would send for `table`."""
	sent = []
	adapter = adapter_module.Adapter.__new__(adapter_module.Adapter)

	async def _execute(sql, *_a):
		sent.append(sql)

	adapter.execute = _execute
	asyncio.run(adapter.create_table(table))
	return sent[0]


def test_declared_indexes_reach_the_create_table(adapter_module):
	sql = _ddl(adapter_module, dict(
		tname="cls_results",
		columns=[dict(cname="key", ctype="VARCHAR(191)", notnull=True),
		         dict(cname="aoe2_match_id", ctype="BIGINT")],
		primary_keys=["key", "aoe2_match_id"],
		indexes=[("cls_results_match", ["aoe2_match_id"])],
	))

	assert "INDEX `cls_results_match` (`aoe2_match_id`)" in sql
	# ...and it is an ordinary secondary index, not a uniqueness constraint that
	# would reject the many rows one match legitimately has.
	assert "UNIQUE KEY `cls_results_match`" not in sql


def test_a_table_declaring_no_index_is_unchanged(adapter_module):
	sql = _ddl(adapter_module, dict(
		tname="plain",
		columns=[dict(cname="id", ctype="BIGINT")],
		primary_keys=["id"],
	))

	assert "INDEX" not in sql
	assert sql == "CREATE TABLE plain (`id` BIGINT, PRIMARY KEY(`id`))"


def test_indexes_and_unique_keys_coexist(adapter_module):
	sql = _ddl(adapter_module, dict(
		tname="both",
		columns=[dict(cname="a", ctype="BIGINT"), dict(cname="b", ctype="BIGINT")],
		primary_keys=["a"],
		unique_keys=[("uniq_b", ["b"])],
		indexes=[("idx_b", ["b"])],
	))

	assert "UNIQUE KEY `uniq_b` (`b`)" in sql
	assert "INDEX `idx_b` (`b`)" in sql


def test_a_multi_column_index_renders_in_declaration_order(adapter_module):
	sql = _ddl(adapter_module, dict(
		tname="multi",
		columns=[dict(cname="a", ctype="BIGINT"), dict(cname="b", ctype="BIGINT")],
		indexes=[("idx_ab", ["a", "b"])],
	))

	assert "INDEX `idx_ab` (`a`, `b`)" in sql
