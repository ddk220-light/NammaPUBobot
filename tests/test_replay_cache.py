"""The replay cache rule the LIVE download path depends on
(utils/replay/download.download_replay returns the cached path, unfetched, when
is_cached says yes).

Imported directly rather than through utils.replay.download: cache.py is
stdlib-only precisely so this stays runnable on CI's pytest-only install, with
neither `requests` nor the vendored mgz fork present. It inherits that property,
and this test, from the retired utils/replay_quiz/manifest.py.
"""
from utils.replay.cache import is_cached, replay_path


def _write(tmp_path, gid, size):
    (tmp_path / f"{gid}.aoe2record").write_bytes(b"x" * size)
    return str(tmp_path)


def test_replay_path_names_the_file_after_the_match_id():
    assert replay_path(501, cache_dir="/c") == "/c/501.aoe2record"


def test_a_downloaded_replay_counts_as_cached(tmp_path):
    assert is_cached(501, cache_dir=_write(tmp_path, 501, 2500000)) is True


def test_a_missing_replay_is_not_cached(tmp_path):
    assert is_cached(501, cache_dir=str(tmp_path)) is False


def test_a_zero_byte_leftover_is_not_cached(tmp_path):
    """A truncated download must be re-fetched. Counting it as a hit wedges that
    match forever: download_replay hands back the empty file without fetching,
    and the parser fails on it every sweep."""
    assert is_cached(501, cache_dir=_write(tmp_path, 501, 0)) is False
