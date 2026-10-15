"""
期货合约月份与换月逻辑。

美债期货（如 10Y Treasury TY）为季度合约，交割月份为 3/6/9/12 月，
月份代码分别为 H/M/U/Z。在每个调仓日，需动态选择近月（front month）合约。

Bloomberg 命名规则：
- 活跃合约年份代码 1 位（如 Z6 = 2026 年 12 月）
- 已到期合约年份代码 2 位（如 Z24 = 2024 年 12 月）
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional, Set

from src.data.option_ticker import build_future_ticker, parse_future_ticker


QUARTERLY_MONTHS = [3, 6, 9, 12]


def next_quarterly_month(year: int, month: int) -> dt.date:
    """
    返回 as_of_date 之后（含当月）的最近季度月首日。

    若当前月本身就是季度月，则返回当月首日；否则返回下一个季度月首日。
    """
    if month in QUARTERLY_MONTHS:
        return dt.date(year, month, 1)
    next_month = min((m for m in QUARTERLY_MONTHS if m > month), default=3)
    if next_month <= month:
        year += 1
    return dt.date(year, next_month, 1)


def following_quarterly_month(year: int, month: int) -> dt.date:
    """返回指定月份之后的下一个季度月首日。"""
    if month in QUARTERLY_MONTHS:
        idx = QUARTERLY_MONTHS.index(month)
        next_idx = (idx + 1) % 4
        next_month = QUARTERLY_MONTHS[next_idx]
        if next_idx == 0:
            year += 1
    else:
        next_month = min((m for m in QUARTERLY_MONTHS if m > month), default=3)
        if next_month <= month:
            year += 1
    return dt.date(year, next_month, 1)


def front_month_ticker(
    as_of_date: dt.date,
    product_prefix: str,
    roll_on_first_day_of_delivery_month: bool = True,
    reference_date: dt.date | None = None,
) -> str:
    """
    返回 as_of_date 对应的近月期货 ticker。

    Args:
        as_of_date: 当前日期。
        product_prefix: 产品代码，例如 "TY"。
        roll_on_first_day_of_delivery_month: True 表示进入交割月首日后换月至下季月；
            False 表示在交割月内仍持有该交割月合约。
        reference_date: 决定年份代码为 1 位还是 2 位，默认为今天。
            若回测历史数据，建议设置为下载当天的日期。

    Returns:
        近月期货 ticker，例如 "TYH9 Comdty" 或 "TYH24 Comdty"。
    """
    delivery_start = next_quarterly_month(as_of_date.year, as_of_date.month)

    if roll_on_first_day_of_delivery_month and as_of_date >= delivery_start:
        # 已进入交割月，换月至下一季度月
        delivery_start = following_quarterly_month(delivery_start.year, delivery_start.month)

    return build_future_ticker(product_prefix, delivery_start.year, delivery_start.month, reference_date)


def active_contracts_in_range(
    start_date: dt.date,
    end_date: dt.date,
    product_prefix: str,
    roll_on_first_day_of_delivery_month: bool = True,
    reference_date: dt.date | None = None,
) -> List[str]:
    """
    返回回测区间内所有可能成为近月的期货合约 ticker 列表。

    Args:
        start_date: 回测起始日。
        end_date: 回测终止日。
        product_prefix: 产品代码。
        roll_on_first_day_of_delivery_month: 是否进入交割月后换月。
        reference_date: 决定年份代码格式，默认为今天。

    Returns:
        去重并按交割日期排序的 ticker 列表。
    """
    tickers: Set[str] = set()
    current = start_date
    while current <= end_date:
        tickers.add(
            front_month_ticker(
                current,
                product_prefix,
                roll_on_first_day_of_delivery_month,
                reference_date,
            )
        )
        # 按月步进即可覆盖所有季度合约
        year = current.year + (current.month // 12)
        month = (current.month % 12) + 1
        current = dt.date(year, month, 1)

    return sorted(
        tickers,
        key=lambda t: parse_future_ticker(t, reference_date).delivery_year * 100
        + parse_future_ticker(t, reference_date).delivery_month,
    )


def contract_delivery_date(ticker: str, as_of: dt.date | None = None) -> Optional[dt.date]:
    """返回期货合约的交割月首日。"""
    try:
        info = parse_future_ticker(ticker, as_of)
        return dt.date(info.delivery_year, info.delivery_month, 1)
    except ValueError:
        return None
