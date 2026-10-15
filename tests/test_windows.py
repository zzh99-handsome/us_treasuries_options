"""
训练窗口测试。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src.core.windows import ExpandingWindow, RollingWindow, create_window


def test_rolling_window_start() -> None:
    """RollingWindow 起始日期应正确回退 train_months 个月。"""
    window = RollingWindow(train_months=3)
    rebalance = dt.date(2020, 4, 1)
    assert window.get_train_start(rebalance) == dt.date(2020, 1, 1)
    assert window.get_train_end(rebalance) == dt.date(2020, 3, 31)


def test_rolling_window_cross_year() -> None:
    """跨年场景。"""
    window = RollingWindow(train_months=3)
    rebalance = dt.date(2020, 2, 1)
    assert window.get_train_start(rebalance) == dt.date(2019, 11, 1)


def test_expanding_window_start() -> None:
    """ExpandingWindow 起始日期固定。"""
    start = dt.date(2019, 1, 1)
    window = ExpandingWindow(start_date=start, min_months=6)
    rebalance = dt.date(2020, 1, 1)
    assert window.get_train_start(rebalance) == start
    assert window.get_train_end(rebalance) == dt.date(2019, 12, 31)
    assert window.is_valid(rebalance)


def test_expanding_window_invalid() -> None:
    """未达最小训练长度时 is_valid 返回 False。"""
    start = dt.date(2020, 1, 1)
    window = ExpandingWindow(start_date=start, min_months=6)
    assert not window.is_valid(dt.date(2020, 3, 1))


def test_window_filter_data() -> None:
    """filter_data 应正确截取训练窗口数据（起始日为窗口内首个交易日）。"""
    dates = pd.date_range("2020-01-01", "2020-06-30", freq="B")
    data = pd.Series(range(len(dates)), index=dates)
    window = RollingWindow(train_months=3)
    filtered = window.filter_data(data, dt.date(2020, 5, 1))
    # 2020-02-01 是周六，首个交易日为 2020-02-03
    assert filtered.index[0].date() == dt.date(2020, 2, 3)
    assert filtered.index[-1].date() == dt.date(2020, 4, 30)


def test_create_window_factory() -> None:
    """工厂函数按类型创建窗口。"""
    rolling = create_window("rolling", 6)
    assert isinstance(rolling, RollingWindow)

    expanding = create_window("expanding", 6, dt.date(2020, 1, 1))
    assert isinstance(expanding, ExpandingWindow)

    with pytest.raises(ValueError):
        create_window("unknown", 6)
