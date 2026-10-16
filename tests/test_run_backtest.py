from __future__ import annotations

from src.core.config_validator import resolve_ratio_grid


def test_ratio_grid_uses_tenth_steps() -> None:
    ratios = resolve_ratio_grid({"ratio_range": {"min": 0.5, "max": 2.5, "step": 0.1}})
    assert len(ratios) == 21
    assert ratios[0] == 0.5
    assert ratios[-1] == 2.5
    assert all(abs((right - left) - 0.1) < 1e-9 for left, right in zip(ratios, ratios[1:]))


def test_ratio_grid_rejects_zero_ratio() -> None:
    import pytest

    with pytest.raises(ValueError, match="0 < min"):
        resolve_ratio_grid({"ratio_range": {"min": 0, "max": 2, "step": 0.1}})

    with pytest.raises(ValueError, match="greater than 0"):
        resolve_ratio_grid({"ratios": [0.0, 1.0]})
