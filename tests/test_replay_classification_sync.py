import asyncio

from bot.replay_stats import classification_sync


class FakeDB:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, args):
        self.calls.append(("execute", sql, args))

    async def insert_many(self, table, rows, on_dublicate=None):
        self.calls.append(("insert_many", table, list(rows), on_dublicate))


def test_write_classification_rows_replaces_match_rows():
    fake = FakeDB()
    result_rows = [{
        "key": "knight_rush",
        "aoe2_match_id": 77,
        "player_number": 1,
        "profile_id": 5,
        "identity": "Al",
        "civ": "Franks",
        "team": "1",
        "winner": 1,
        "played_at": 123,
    }]
    metric_rows = [{
        "key": "knight_rush",
        "aoe2_match_id": 77,
        "player_number": 1,
        "metric": "knights_pre_imperial",
        "value": 9.0,
    }]

    asyncio.run(classification_sync.write_classification_rows(77, result_rows, metric_rows, fake))

    assert fake.calls[0] == ("execute", "DELETE FROM cls_result_metrics WHERE aoe2_match_id=%s", [77])
    assert fake.calls[1] == ("execute", "DELETE FROM cls_results WHERE aoe2_match_id=%s", [77])
    assert fake.calls[2] == ("insert_many", "cls_results", result_rows, "replace")
    assert fake.calls[3] == ("insert_many", "cls_result_metrics", metric_rows, "replace")


def test_sync_match_uses_live_classifier_shape():
    fake = FakeDB()
    extracted = {
        "match": {"aoe2_match_id": 88},
        "players": [
            {
                "player_number": 1,
                "profile_id": 10,
                "identity": "Archer",
                "civ": "Mayans",
                "team": "1",
                "winner": True,
                "feudal_s": 600,
                "castle_s": 1200,
            },
            {
                "player_number": 2,
                "profile_id": 11,
                "identity": "Boomer",
                "civ": "Franks",
                "team": "2",
                "winner": False,
                "feudal_s": 600,
                "castle_s": 700,
            },
        ],
        "techs": [],
        "events": [
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 5, "t_s": 700}
        ],
    }

    counts = asyncio.run(classification_sync.sync_match(extracted, 456, fake))

    assert counts[0] >= 1
    inserted_results = fake.calls[2][2]
    assert any(r["key"] == "archer_rush" and r["aoe2_match_id"] == 88 for r in inserted_results)
