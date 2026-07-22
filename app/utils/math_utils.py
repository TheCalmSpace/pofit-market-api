"""
Shared mathematical utilities for the POFIT backend.

These helpers provide safe, reusable financial calculations that are used
throughout the Metrics Engine and Scoring Engine.
"""

from typing import Iterable, Optional


def safe_divide(
    numerator: Optional[float],
    denominator: Optional[float],
) -> Optional[float]:
    """
    Safely divide two numbers.

    Returns None if either value is missing or the denominator is zero.
    """

    if numerator is None or denominator is None:
        return None

    if denominator == 0:
        return None

    return numerator / denominator


def calculate_cagr(
    start_value: Optional[float],
    end_value: Optional[float],
    years: int,
) -> Optional[float]:
    """
    Calculate Compound Annual Growth Rate (CAGR).

    Returns percentage growth.

    Example:
        Start = 100
        End = 133.1
        Years = 3

        Returns:
            10.0
    """

    if (
        start_value is None
        or end_value is None
        or years <= 0
        or start_value <= 0
        or end_value <= 0
    ):
        return None

    cagr = ((end_value / start_value) ** (1 / years) - 1) * 100

    return round(cagr, 2)


def percentage_change(
    old_value: Optional[float],
    new_value: Optional[float],
) -> Optional[float]:
    """
    Calculate percentage change.

    Returns:
        ((new-old)/old)*100
    """

    if (
        old_value is None
        or new_value is None
        or old_value == 0
    ):
        return None

    return round(((new_value - old_value) / old_value) * 100, 2)


def average(
    values: Iterable[Optional[float]],
) -> Optional[float]:
    """
    Return the average of numeric values.

    Ignores None values.
    """

    filtered = [v for v in values if v is not None]

    if not filtered:
        return None

    return round(sum(filtered) / len(filtered), 2)


def latest(values: Iterable[Optional[float]]) -> Optional[float]:
    """
    Return the first available non-null value.

    Useful for historical datasets sorted newest -> oldest.
    """

    for value in values:
        if value is not None:
            return value

    return None


def trend_direction(
    old_value: Optional[float],
    new_value: Optional[float],
) -> Optional[str]:
    """
    Determine trend direction between two values.
    """

    if old_value is None or new_value is None:
        return None

    if new_value > old_value:
        return "Increasing"

    if new_value < old_value:
        return "Decreasing"

    return "Stable"