"""Resume/todo filtering for the replay downloader (utils/replay_quiz/manifest.py).

The manifest (data/replay_manifest.csv) is git-tracked; the replay cache
(data/replays/) is gitignored. So "the manifest says this parsed fine" says
nothing about whether the file is still on THIS machine — a fresh checkout has
every row and zero files. The resume filter must consult both, or the
downloader concludes there is nothing to do and downloads nothing.

manifest.py is deliberately free of `requests`/`mgz` imports so these tests run
on CI's pytest-only install.
"""
import csv
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "replay_quiz_manifest",
    pathlib.Path(__file__).resolve().parents[1] / "utils" / "replay_quiz" / "manifest.py")
mf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mf)


def _row(gid, parse_ok=True, error=""):
    return dict(aoe2_match_id=str(gid), profile_id="123", bytes="2500000",
                save_version="67.4", body_ops="40000", minutes="31.5",
                body_parse_ok=str(parse_ok), error=error)


def _cache(tmp_path, *gids, size=2500000):
    d = tmp_path / "replays"
    d.mkdir(exist_ok=True)
    for g in gids:
        (d / f"{g}.aoe2record").write_bytes(b"x" * size)
    return str(d)


def test_parsed_row_with_missing_cache_file_is_still_pending(tmp_path):
    # The fresh-checkout case: manifest present, cache empty.
    manifest = {"501": _row(501)}
    assert mf.pending_ids([501], manifest, cache_dir=_cache(tmp_path)) == [501]


def test_parsed_row_with_cache_file_present_is_done(tmp_path):
    manifest = {"501": _row(501)}
    assert mf.pending_ids([501], manifest, cache_dir=_cache(tmp_path, 501)) == []


def test_id_absent_from_manifest_is_pending(tmp_path):
    assert mf.pending_ids([501], {}, cache_dir=_cache(tmp_path, 501)) == [501]


def test_failed_parse_is_pending_even_when_cached(tmp_path):
    # Re-attempted on purpose: an mgz upgrade can make a cached file parse.
    manifest = {"501": _row(501, parse_ok=False, error="Struct: bad")}
    assert mf.pending_ids([501], manifest, cache_dir=_cache(tmp_path, 501)) == [501]


def test_zero_byte_cache_file_counts_as_missing(tmp_path):
    # download_replay() treats a 0-byte file as not-cached; the filter must agree,
    # or a truncated download is silently accepted as done forever.
    manifest = {"501": _row(501)}
    assert mf.pending_ids([501], manifest, cache_dir=_cache(tmp_path, 501, size=0)) == [501]


def test_bool_true_from_an_in_run_row_is_recognised(tmp_path):
    # Rows written during a run hold a real bool; rows read back from CSV hold "True".
    manifest = {"501": dict(_row(501), body_parse_ok=True)}
    assert mf.pending_ids([501], manifest, cache_dir=_cache(tmp_path, 501)) == []


def test_pending_preserves_input_order(tmp_path):
    gids = [503, 502, 501]
    manifest = {"502": _row(502)}
    cache = _cache(tmp_path, 502)
    assert mf.pending_ids(gids, manifest, cache_dir=cache) == [503, 501]


def test_manifest_round_trips_through_csv(tmp_path):
    path = str(tmp_path / "replay_manifest.csv")
    rows = {"501": _row(501), "9": _row(9, parse_ok=False, error="http_404")}
    mf.write_manifest(rows, path=path)
    assert mf.load_manifest(path=path) == rows
    with open(path, newline="") as f:
        r = csv.reader(f)
        assert next(r) == mf.FIELDS
        assert [line[0] for line in r] == ["501", "9"]  # newest id first


def test_download_module_is_importable_the_way_the_bot_imports_it(monkeypatch):
    """bot/replay_stats/fetch.py does `from utils.replay_quiz import download`
    with only the repo ROOT on sys.path, so download.py must not depend on its
    own directory being importable.

    Its siblings (extract, quiz, ...) are script-only and use bare sibling
    imports freely; download.py is the one module the bot also imports, and a
    bare `from manifest import ...` there breaks the live replay-fetch path.
    """
    import sys
    import types

    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("requests", "mgz", "mgz.fast", "mgz.util"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["mgz"].fast = sys.modules["mgz.fast"]
    sys.modules["mgz.util"].get_save_version = lambda *_a, **_k: None
    # Drop any cached copy so the import actually re-executes.
    for name in list(sys.modules):
        if name.startswith("utils.replay_quiz"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(root))

    from utils.replay_quiz import download

    assert callable(download.pending_ids)
    assert callable(download.download_replay)
