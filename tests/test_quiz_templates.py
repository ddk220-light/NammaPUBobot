"""Tests for the pure question templates in utils/quiz_gen/templates.py.

Loaded by file path (utils is not an importable package under the bot test shim),
mirroring utils/preview_insights.py's loader."""
from __future__ import annotations

import importlib.util
import os
import random

_PATH = os.path.join(os.path.dirname(__file__), "..", "utils", "quiz_gen", "templates.py")
_spec = importlib.util.spec_from_file_location("quiz_templates", _PATH)
t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t)


def _siege():
	return [
		{"unit_name": "Ram", "pierce_armor": 180, "hp": 270},
		{"unit_name": "Mangonel", "pierce_armor": 6, "hp": 50},
		{"unit_name": "Scorpion", "pierce_armor": 5, "hp": 40},
		{"unit_name": "Siege Tower", "pierce_armor": 6, "hp": 220},
	]


def test_superlative_max_picks_true_max_and_4_distinct_options():
	q = t.superlative(_siege(), stat="pierce_armor", label="pierce armor",
					  category="armor", rng=random.Random(0))[0]
	assert q["options"][q["correct_index"]] == "Ram"     # 180 is the max
	assert len(q["options"]) == 4 and len(set(q["options"])) == 4


def test_superlative_min_variant():
	q = t.superlative(_siege(), stat="pierce_armor", label="pierce armor",
					  category="armor", rng=random.Random(0), want_max=False)[0]
	assert q["options"][q["correct_index"]] == "Scorpion"   # 5 is the min


def test_superlative_needs_four_units():
	assert t.superlative(_siege()[:3], stat="hp", label="HP", category="x",
						 rng=random.Random(0)) == []


def test_superlative_rejects_tie_at_extreme():
	# two units tie for the highest HP -> ambiguous -> no question
	tied = [
		{"unit_name": "A", "hp": 100},
		{"unit_name": "B", "hp": 100},
		{"unit_name": "C", "hp": 50},
		{"unit_name": "D", "hp": 40},
	]
	assert t.superlative(tied, stat="hp", label="HP", category="x",
						 rng=random.Random(0)) == []


def test_bonus_membership_only_correct_has_class():
	units = [
		{"unit_name": "Mameluke", "attacks": {"30": 10}},
		{"unit_name": "Knight", "attacks": {"4": 14}},
		{"unit_name": "Archer", "attacks": {"4": 4}},
		{"unit_name": "Skirmisher", "attacks": {"15": 3}},
	]
	q = t.bonus_membership(units, armor_class_id="30", class_name="Camels",
						   category="bonus", rng=random.Random(1))[0]
	assert q["options"][q["correct_index"]] == "Mameluke"


def test_bonus_membership_empty_when_no_yes():
	units = [{"unit_name": f"u{i}", "attacks": {"4": 1}} for i in range(5)]
	assert t.bonus_membership(units, armor_class_id="30", class_name="Camels",
							  category="bonus", rng=random.Random(0)) == []


def test_only_one_with_mechanic():
	q = t.only_one_with_mechanic(
		special_names=["Coustillier"],
		normal_names=["Knight", "Archer", "Skirmisher", "Scout"],
		mechanic_label="a bleed effect", category="mechanic", rng=random.Random(2))[0]
	assert q["options"][q["correct_index"]] == "Coustillier"
	assert "Coustillier" not in [o for i, o in enumerate(q["options"]) if i != q["correct_index"]]


def test_question_shape_is_complete():
	q = t.superlative(_siege(), stat="hp", label="HP", category="hp",
					  rng=random.Random(0))[0]
	for k in ("id", "category", "difficulty", "prompt", "options", "correct_index",
			  "explanation", "source"):
		assert k in q
	assert 0 <= q["correct_index"] < 4
