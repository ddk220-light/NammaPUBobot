# -*- coding: utf-8 -*-
"""Idle jobs reconcile once, then leave enough silence for Railway sleep."""
import asyncio
import importlib

import nammaoe2bot.features.betting.flow as betting_flow

quiz_module = importlib.import_module("nammaoe2bot.features.quiz.jobs")


class _IdlePredictions:
	async def unsettled_books(self, _before):
		return []

	async def abandoned_books(self, _before):
		return []

	async def has_live_books(self):
		return False


class _IdleQuiz:
	async def enabled_configs(self):
		return []

	async def due_to_close(self, _now):
		return []

	async def next_open_close_at(self):
		return None


def test_predictions_disarm_after_empty_recovery(monkeypatch):
	monkeypatch.setattr(betting_flow, "store", _IdlePredictions())
	monkeypatch.setattr(betting_flow.time, "time", lambda: 1_000)
	job = betting_flow.PredictionJobs()
	asyncio.run(job._run())
	assert job._active is False
	assert job.next_run == 1_000 + job.RECOVERY_INTERVAL


def test_quiz_schedules_exact_daily_recovery_when_empty(monkeypatch):
	monkeypatch.setattr(quiz_module, "store", _IdleQuiz())
	monkeypatch.setattr(quiz_module.time, "time", lambda: 1_000)
	job = quiz_module.QuizJobs()
	asyncio.run(job._run())
	assert job.next_run == 1_000 + job.RECOVERY_INTERVAL


def test_quiz_config_uses_exact_deadline_not_thirty_second_poll():
	job = quiz_module.QuizJobs()
	cfg = {"test_interval": 3_600, "last_post_at": 10_000}
	assert job._config_due_at(cfg, 10_100) == 13_600
