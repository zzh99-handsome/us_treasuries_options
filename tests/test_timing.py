"""
择时因子测试。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from src.timing.base import TimingFactor
from src.timing.bull_bear import BullBearRegime


def test_bull_bear_neutral_without_data() -> None:
    """未拟合数据时返回 neutral。"""
    factor = BullBearRegime()
    assert factor.regime(dt.date(2020, 6, 1)) == "neutral"


def test_bull_bear_insufficient_history() -> None:
    """数据长度不足时返回 neutral。"""
    dates = pd.date_range("2020-01-01", periods=30, freq="B")
    futures = pd.DataFrame({"PX_LAST": range(30)}, index=dates)
    factor = BullBearRegime()
    factor.fit({"futures": futures})
    assert factor.regime(dt.date(2020, 2, 14)) == "neutral"


def test_bull_bear_identifies_bull() -> None:
    """短期均线高于长期均线时判断为 bull。"""
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    # 价格持续上涨
    prices = [100.0 + i * 0.1 for i in range(len(dates))]
    futures = pd.DataFrame({"PX_LAST": prices}, index=dates)
    factor = BullBearRegime()
    factor.fit({"futures": futures})
    assert factor.regime(dates[-1].date()) == "bull"


def test_bull_bear_identifies_bear() -> None:
    """短期均线低于长期均线时判断为 bear。"""
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    # 价格持续下跌
    prices = [200.0 - i * 0.1 for i in range(len(dates))]
    futures = pd.DataFrame({"PX_LAST": prices}, index=dates)
    factor = BullBearRegime()
    factor.fit({"futures": futures})
    assert factor.regime(dates[-1].date()) == "bear"


def test_timing_factor_abstract_methods() -> None:
    """TimingFactor 是抽象基类，不能被实例化。"""
    try:
        TimingFactor()
    except TypeError:
        pass
    else:
        raise AssertionError("TimingFactor 应无法直接实例化")
