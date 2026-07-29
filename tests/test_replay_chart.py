"""Pure helpers behind the APM chart's legibility, tested without matplotlib.

Two findings from validating the renderer against 9 real 8-player matches drove these:
same-team lines share a hue family and blurred together during crossing-heavy stretches
(worst on a 51.6-minute game), and a CJK player name rendered as empty tofu boxes in the
legend because the production image (python:3.11-slim) ships no CJK fonts and matplotlib's
bundled DejaVu Sans has no glyphs for them.
"""
from bot.replay_stats.chart import (
    _APM_LINESTYLES,
    _APM_TEAM_COLOURS,
    _apm_label,
    _apm_line_style,
    _short,
)


# ── per-player colour + linestyle ────────────────────────────────────────


def test_teams_get_distinct_hue_families():
    blue, _ = _apm_line_style(0, 0)
    red, _ = _apm_line_style(1, 0)
    assert blue in _APM_TEAM_COLOURS[0]
    assert red in _APM_TEAM_COLOURS[1]
    assert blue != red


def test_every_player_in_a_4v4_team_is_uniquely_styled():
    # The whole point: with 4 players a side, hue step and dash pattern must BOTH vary,
    # so two same-team lines never look alike where they cross.
    for team in (0, 1):
        styles = [_apm_line_style(team, i) for i in range(4)]
        assert len({c for c, _ in styles}) == 4, "each player needs their own hue step"
        assert len({ls for _, ls in styles}) == 4, "each player needs their own dash pattern"


def test_linestyle_varies_even_when_the_hue_repeats():
    # Beyond 4 players a side both palettes wrap; they must wrap in step, not independently,
    # or two players would land on an identical colour+dash pair.
    a = _apm_line_style(0, 0)
    b = _apm_line_style(0, len(_APM_TEAM_COLOURS[0]))
    assert a == b, "wrap should be in lockstep, giving a predictable repeat"
    assert _apm_line_style(0, 1)[1] == _APM_LINESTYLES[1]


def test_first_player_of_each_team_stays_solid():
    # Solid reads strongest; keep it for the first line drawn rather than starting on dots.
    assert _apm_line_style(0, 0)[1] == "-"
    assert _apm_line_style(1, 0)[1] == "-"


def test_players_without_a_team_still_separate():
    # teams.get() yields None for anyone the roster didn't map; they used to all collapse
    # onto one grey solid line.
    styles = [_apm_line_style(None, i) for i in range(4)]
    assert len(set(styles)) == 4


# ── legend labels ────────────────────────────────────────────────────────


def test_ascii_name_is_untouched():
    assert _apm_label("Deepak", 1) == "Deepak"


def test_all_cjk_name_falls_back_to_the_player_slot():
    # Reproduces 100% on python:3.11-slim: no CJK font, so the legend entry is blank boxes.
    assert _apm_label("一般般", 3) == "Player 3"


def test_mixed_name_keeps_the_renderable_part():
    assert _apm_label("Bob一般般", 2) == "Bob"


def test_accented_and_cyrillic_names_survive():
    # DejaVu Sans covers Latin-1/Latin-Extended/Greek/Cyrillic — mangling these would be a
    # regression, so the filter must be glyph-coverage-shaped, not a blunt isascii() test.
    assert _apm_label("Renée", 1) == "Renée"
    assert _apm_label("Ünal", 1) == "Ünal"
    assert _apm_label("Иван", 1) == "Иван"


def test_emoji_are_dropped_but_the_name_survives():
    assert _apm_label("Bob 🔥", 1) == "Bob"


def test_empty_and_missing_names_fall_back():
    assert _apm_label("", 4) == "Player 4"
    assert _apm_label(None, 4) == "Player 4"
    assert _apm_label("   ", 4) == "Player 4"


def test_long_names_are_still_truncated():
    label = _apm_label("A" * 40, 1)
    assert len(label) == 16
    assert label.endswith(".")


def test_shared_short_helper_is_left_alone():
    # _apm_label must not become _short: the timeline and growth-curve renderers label a
    # single caller-chosen player and have to render that name verbatim.
    assert _short("一般般") == "一般般"
