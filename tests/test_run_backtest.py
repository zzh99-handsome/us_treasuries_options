from __future__ import annotations

import pandas as pd

from run_backtest import build_all_option_tickers, resolve_ratio_grid
from src.data.option_ticker import build_option_ticker, list_option_series


def test_ratio_grid_excludes_zero_and_uses_tenth_steps() -> None:
    ratios = resolve_ratio_grid({"ratio_range": {"min": 0.1, "max": 2, "step": 0.1}})
    assert len(ratios) == 20
    assert ratios[0] == 0.1
    assert ratios[-1] == 2.0
    assert all(abs((right - left) - 0.1) < 1e-9 for left, right in zip(ratios, ratios[1:]))


def test_ratio_grid_rejects_zero_ratio() -> None:
    import pytest

    with pytest.raises(ValueError, match="0 < min"):
        resolve_ratio_grid({"ratio_range": {"min": 0, "max": 2, "step": 0.1}})

    with pytest.raises(ValueError, match="range"):
        resolve_ratio_grid({"ratios": [0.0, 1.0]})


def test_build_all_option_tickers_uses_monthly_entry_price_range() -> None:
    """Ticker prefetch includes strikes centered on each month's actual futures price."""
    futures_data = {
        "TYH19 Comdty": pd.DataFrame(
            {"PX_LAST": [122.1875]},
            index=pd.DatetimeIndex(["2019-01-02"]),
        )
    }
    config = {
        "product_code": "TY",
        "product_prefix": "TY",
        "option_type": "P",
        "strike_step": 0.25,
        "strike_half_width": 3.0,
        "roll_on_first_day_of_delivery_month": True,
    }

    tickers = build_all_option_tickers(futures_data, config)

    assert "TYG9P 122.25 Comdty" in tickers
    assert "TYG9P 119.25 Comdty" in tickers
    assert "TYG9P 125.25 Comdty" in tickers
    assert "TYG9P 105 Comdty" not in tickers
    eligible_series = [
        series
        for series in list_option_series(
            pd.Timestamp("2019-01-02").date(),
            pd.Timestamp("2019-01-02").date(),
            "TYH19 Comdty",
        )
        if (series.expiry_date - pd.Timestamp("2019-01-02").date()).days >= 2
    ]
    assert len(eligible_series) > 1
    assert all(
        build_option_ticker("TY", series, "P", 122.25) in tickers
        for series in eligible_series
    )
