"""
期货日历与近月合约选择测试。
"""

from __future__ import annotations

import datetime as dt

from src.data.futures_calendar import active_contracts_in_range, front_month_ticker
from src.data.option_ticker import build_future_ticker


REF_DATE = dt.date(2026, 1, 1)


def test_build_future_ticker() -> None:
    """构造期货 ticker。"""
    # 以 2026 年为参考，2026 年合约活跃，用 1 位年份
    assert build_future_ticker("TY", 2026, 12, REF_DATE) == "TYZ6 Comdty"
    # 2019 年合约已到期，用 2 位年份
    assert build_future_ticker("TY", 2019, 3, REF_DATE) == "TYH19 Comdty"


def test_front_month_ticker_jan_2019() -> None:
    """2019-01-15 的近月合约应为 TYH19（Mar 2019）。"""
    assert front_month_ticker(dt.date(2019, 1, 15), "TY", reference_date=REF_DATE) == "TYH19 Comdty"


def test_front_month_ticker_apr_2019() -> None:
    """2019-04-01 的近月合约应为 TYM19（Jun 2019）。"""
    assert front_month_ticker(dt.date(2019, 4, 1), "TY", reference_date=REF_DATE) == "TYM19 Comdty"


def test_front_month_ticker_rolls_in_delivery_month() -> None:
    """进入交割月后换月至下一季月。"""
    assert front_month_ticker(
        dt.date(2019, 3, 1), "TY",
        roll_on_first_day_of_delivery_month=True,
        reference_date=REF_DATE,
    ) == "TYM19 Comdty"


def test_front_month_ticker_stays_in_delivery_month() -> None:
    """不启用换月时，交割月内仍持有该月合约。"""
    assert front_month_ticker(
        dt.date(2019, 3, 1), "TY",
        roll_on_first_day_of_delivery_month=False,
        reference_date=REF_DATE,
    ) == "TYH19 Comdty"


def test_active_contracts_in_range() -> None:
    """返回区间内所有近月合约。"""
    tickers = active_contracts_in_range(
        dt.date(2019, 1, 1),
        dt.date(2019, 12, 31),
        "TY",
        reference_date=REF_DATE,
    )
    assert "TYH19 Comdty" in tickers
    assert "TYM19 Comdty" in tickers
    assert "TYU19 Comdty" in tickers
    assert "TYZ19 Comdty" in tickers
