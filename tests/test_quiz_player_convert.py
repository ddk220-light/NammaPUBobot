import importlib.util
import pathlib
import random

spec = importlib.util.spec_from_file_location(
    "convert_player_bank",
    pathlib.Path(__file__).resolve().parents[1] / "utils" / "quiz_gen" / "convert_player_bank.py")
cpb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpb)

_TOP4 = dict(
    question_id="vil_total|top4|best", category="Villagers", format="top4", ask="best",
    metric_id="vil_total", label="Most villagers / game",
    question="Who makes the **most villagers / game**?",
    options_json='[{"identity": "alice", "value": "211.19", "elo": 2759},'
                 ' {"identity": "bob", "value": "188.85", "elo": null},'
                 ' {"identity": "cara", "value": "182.61", "elo": 1566},'
                 ' {"identity": "dan", "value": "178.87", "elo": 1007}]',
    answer="alice",
    refs_json='[{"identity": "cara", "civ": "Celts", "value": "791", "match_id": 442000290}]',
    elo_lo=None, elo_hi=None, closeness=0.894)


def test_answer_index_is_correct_after_shuffle():
    q = cpb.convert_record(_TOP4, random.Random(1))
    assert q["correct_index"] == q["correct_indices"][0]
    assert q["options"][q["correct_index"]].startswith("alice")
    assert q["multi"] is False and q["source"] == "player"


def test_options_never_leak_the_metric_value():
    q = cpb.convert_record(_TOP4, random.Random(2))
    for opt in q["options"]:
        for val in ("211.19", "188.85", "182.61", "178.87"):
            assert val not in opt          # values live in the reveal, not the options


def test_explanation_shows_values_and_a_reference_game():
    q = cpb.convert_record(_TOP4, random.Random(3))
    assert "211.19" in q["explanation"] and "alice" in q["explanation"]
    assert "442000290" in q["explanation"]


def test_schema_is_structurally_valid():
    q = cpb.convert_record(_TOP4, random.Random(4))
    assert len(q["options"]) == 4 == len(set(q["options"]))
    assert 0 <= q["correct_index"] < 4
    assert q["difficulty"] in ("easy", "medium", "hard")
    assert q["category"] == "Villagers" and q["question_type"] == "top4"


def test_answer_slot_moves_across_questions():
    # over many distinct questions the answer should not always land on slot 0
    slots = set()
    for i in range(20):
        rec = dict(_TOP4, question_id=f"m{i}|top4|best")
        slots.add(cpb.convert_record(rec, random.Random(i))["correct_index"])
    assert len(slots) > 1
