from utils.replay_quiz.extract import _nearest, _perimeter


def test_nearest_returns_min_distance():
    assert _nearest((0.0, 0.0), [(3.0, 4.0), (6.0, 8.0)]) == 5.0


def test_nearest_none_when_no_points_or_no_pos():
    assert _nearest((0.0, 0.0), []) is None
    assert _nearest(None, [(1.0, 1.0)]) is None


def test_perimeter_of_3_4_5_triangle():
    # right triangle legs 3 and 4 -> sides 3,4,5 -> perimeter 12
    assert round(_perimeter([(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)]), 3) == 12.0


def test_perimeter_none_when_fewer_than_two():
    assert _perimeter([(1.0, 1.0)]) is None
    assert _perimeter([]) is None
