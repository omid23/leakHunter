"""The single rounding rule for money (USD floats rounded to cents)."""

from __future__ import annotations


def money(value: float) -> float:
    """Round to cents, half away from zero, without binary float surprises (e.g. 2.675 -> 2.68)."""
    scaled = abs(value) * 100 + 1e-9
    rounded = int(scaled + 0.5) / 100
    return -rounded if value < 0 else rounded


def pct(numerator: float, denominator: float) -> float | None:
    """Percentage change helper rounded to two decimals; ``None`` when the base is zero."""
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)
