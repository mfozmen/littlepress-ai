from src.imposition import _booklet_order


def test_four_pages_use_known_saddle_stitch_order():
    assert _booklet_order(4) == [4, 1, 2, 3]


def test_three_pages_are_padded_to_four():
    order = _booklet_order(3)

    assert len(order) == 4
    assert None in order
    assert set(x for x in order if x is not None) == {1, 2, 3}


def test_eight_pages_interleave_outer_and_inner_sheets():
    # outer sheet front+back then inner sheet front+back
    assert _booklet_order(8) == [8, 1, 2, 7, 6, 3, 4, 5]
