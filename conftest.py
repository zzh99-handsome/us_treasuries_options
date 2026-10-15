"""
pytest 共享 fixture。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Dict

import pandas as pd
import pytest


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """临时缓存目录。"""
    return tmp_path / "cache"


@pytest.fixture
def sample_futures_df() -> pd.DataFrame:
    """合成期货价格序列（2020-01-01 至 2020-12-31，每日价格从 130 缓慢下跌至 120）。"""
    dates = pd.date_range(start="2020-01-01", end="2020-12-31", freq="B")
    n = len(dates)
    prices = 130.0 - (130.0 - 120.0) * (range(n)) / n
    return pd.DataFrame({"PX_LAST": prices}, index=dates)


@pytest.fixture
def sample_options_data(sample_futures_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    合成期权 bid/ask 数据。

    以 ATM 为中心，构造一组 put 的 bid/ask，价格随行权价线性变化。
    """
    dates = sample_futures_df.index
    center = 125.0
    strikes = [round(center + i * 0.25, 6) for i in range(-12, 13)]
    data: Dict[str, pd.DataFrame] = {}
    for s in strikes:
        # 行权价越低，put 越便宜；ask 比 bid 高 1/64
        base = max(0.0, (center - s) + 0.5) * 64.0
        bid = base
        ask = base + 1.0
        df = pd.DataFrame(
            {"PX_BID": bid, "PX_ASK": ask, "PX_LAST": (bid + ask) / 2},
            index=dates,
        )
        data[s] = df
    return data


@pytest.fixture
def sample_config() -> dict:
    """测试用回测配置。"""
    return {
        "product_code": "TY",
        "product_prefix": "TY",
        "roll_on_first_day_of_delivery_month": True,
        "option_type": "P",
        "strike_step": 0.25,
        "strike_half_width": 3.0,
        "intervals": [0.25, 0.5, 0.75, 1.0],
        "ratios": [1.0, 1.5, 2.0],
        "window_type": "rolling",
        "train_months": 3,
        "risk_free_rate": 0.04,
        "initial_capital": 1_000_000.0,
        "point_value": 1000.0,
        "contracts_per_leg": 1,
        "cache_dir": "./cache",
        "report_dir": "./reports",
        "enable_timing_factor": False,
        "expiry_overrides": {},
        "futures_fields": ["PX_LAST"],
        "option_fields": ["PX_BID", "PX_ASK", "PX_LAST"],
    }
