import pytest

from leakhunter.domain.money import money, pct


@pytest.mark.parametrize(
    ("value", "expected"),
    [(571.392, 571.39), (160.0003, 160.0), (2.675, 2.68), (-27.271, -27.27), (0.005, 0.01), (0.0, 0.0)],
)
def test_money_rounds_half_away_from_zero_to_cents(value, expected):
    assert money(value) == expected


def test_pct_handles_zero_base():
    assert pct(5, 0) is None
    assert pct(725.14, 818.13) == 88.63
