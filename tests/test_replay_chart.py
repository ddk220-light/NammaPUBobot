"""Pure helpers behind the APM chart's legibility, tested without matplotlib.

Two findings from validating the renderer against 9 real 8-player matches drove these:
same-team lines share a hue family and blurred together during crossing-heavy stretches
(worst on a 51.6-minute game), and a CJK player name rendered as empty tofu boxes in the
legend because the production image (python:3.11-slim) ships no CJK fonts and matplotlib's
bundled DejaVu Sans has no glyphs for them.
"""
from bot.replay_stats.chart import (
    _APM_PLAYER_COLOURS,
    _APM_UNKNOWN_COLOUR,
    _apm_label,
    _apm_line_style,
    _short,
)


# ── per-player colour + linestyle ────────────────────────────────────────


def test_colour_is_the_players_in_game_slot_colour():
    # The point of the whole scheme: a player finds their line by the colour they played
    # as, so slot -> colour must be a fixed mapping, never position-dependent.
    for slot in range(1, 9):
        colour, _ = _apm_line_style(slot, 0)
        assert colour == _APM_PLAYER_COLOURS[slot]


def test_all_eight_slots_are_distinct_colours():
    assert len(set(_APM_PLAYER_COLOURS.values())) == 8


def test_colour_does_not_depend_on_team():
    # Slot 3 is green whichever side they are on.
    assert _apm_line_style(3, 0)[0] == _apm_line_style(3, 1)[0]


def test_team_is_carried_by_the_dash_pattern():
    # Colour now identifies the player, so team has to live somewhere else.
    assert _apm_line_style(1, 0)[1] == "-"
    assert _apm_line_style(1, 1)[1] == "--"


def test_every_player_in_a_4v4_is_uniquely_styled():
    # Eight slots across two teams must yield eight distinct colour+dash pairs, or two
    # lines look alike where they cross.
    styles = [_apm_line_style(slot, 0 if slot <= 4 else 1) for slot in range(1, 9)]
    assert len(set(styles)) == 8


def test_unknown_slot_falls_back_without_stealing_a_real_colour():
    # A slot outside 1-8 must not collide with a real player's colour.
    colour, _ = _apm_line_style(99, 0)
    assert colour == _APM_UNKNOWN_COLOUR
    assert colour not in _APM_PLAYER_COLOURS.values()


def test_unmapped_team_is_visibly_different():
    # teams.get() yields None for anyone the roster did not map.
    assert _apm_line_style(1, None)[1] == ":"
    assert _apm_line_style(1, None)[1] not in ("-", "--")


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
