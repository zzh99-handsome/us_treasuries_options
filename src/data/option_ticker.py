"""
期权 ticker 构造与到期日推导工具。

遵循 Bloomberg 美债期货/期权命名规则：
- 活跃合约年份代码为 1 位（如 Z6 = 2026 年 12 月）。
- 已到期合约年份代码为 2 位（如 Z24 = 2024 年 12 月）。
- 期权 ticker 格式：{prefix}{month_code}{year_code}{P/C} {strike} Comdty
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

MONTH_CODES = "FGHJKMNQUVXZ"  # 1..12 月
MONTH_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


class OptionSeries(NamedTuple):
    """一个期权系列（对应某个到期年月）。"""

    year: int
    month: int
    expiry_date: dt.date
    month_code: str
    year_code: str

    @property
    def key(self) -> str:
        """例如 Z6 或 Z24。"""
        return f"{self.month_code}{self.year_code}"


class FutureInfo(NamedTuple):
    """从期货 ticker 解析出的信息。"""

    prefix: str
    month_code: str
    year_code: str
    delivery_year: int
    delivery_month: int


def year_code(year: int, as_of: dt.date | None = None) -> str:
    """
    返回 Bloomberg 年份代码。

    活跃合约（year >= as_of.year）使用 1 位；
    已到期合约（year < as_of.year）使用 2 位。

    Args:
        year: 合约年份。
        as_of: 参考日期，默认为今天。

    Returns:
        "6" 或 "24" 等。
    """
    if as_of is None:
        as_of = dt.date.today()
    if year >= as_of.year:
        return str(year % 10)
    return f"{year % 100:02d}"


def fourth_friday(year: int, month: int) -> dt.date:
    """返回某年某月的第 4 个周五。"""
    first = dt.date(year, month, 1)
    # 周一=0 ... 周五=4；偏移量到第一个周五
    offset = (4 - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 21)


def prev_month(year: int, month: int) -> Tuple[int, int]:
    """返回上一个月的 (year, month)。"""
    if month == 1:
        return year - 1, 12
    return year, month - 1


def parse_future_ticker(ticker: str, as_of: dt.date | None = None) -> FutureInfo:
    """
    从 'TYZ6 Comdty' 或 'TYZ24 Comdty' 解析出期货信息。

    Args:
        ticker: 期货 ticker。
        as_of: 当前日期，用于推断 1 位年份代码的世纪。

    Returns:
        FutureInfo 对象。
    """
    if as_of is None:
        as_of = dt.date.today()

    m = re.match(r"^([A-Z]+)([FGHJKMNQUVXZ])(\d{1,2})\s+Comdty$", ticker.strip())
    if not m:
        raise ValueError(f"无法解析期货代码: {ticker}")

    prefix = m.group(1)
    month_code = m.group(2)
    year_str = m.group(3)
    month = MONTH_CODES.index(month_code) + 1

    if len(year_str) == 1:
        # 活跃合约：1 位年份，按 as_of 推断世纪
        digit = int(year_str)
        base = (as_of.year // 10) * 10 + digit
        if base < as_of.year - 1:
            base += 10
        year_code_str = year_str
    else:
        # 已到期合约：2 位年份，直接解释为 2000+年份
        base = 2000 + int(year_str)
        year_code_str = year_str

    return FutureInfo(
        prefix=prefix,
        month_code=month_code,
        year_code=year_code_str,
        delivery_year=base,
        delivery_month=month,
    )


def month_iter(
    start_ym: Tuple[int, int],
    end_ym: Tuple[int, int],
) -> Iterable[Tuple[int, int]]:
    """逐月迭代 [start, end]（均含）。"""
    y, m = start_ym
    ey, em = end_ym
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def build_future_ticker(
    product_prefix: str,
    year: int,
    month: int,
    as_of: dt.date | None = None,
) -> str:
    """
    构造期货 ticker。

    Args:
        product_prefix: 产品代码，例如 "TY"。
        year: 完整年份，例如 2026。
        month: 交割月份 1..12。
        as_of: 参考日期，决定年份代码为 1 位还是 2 位。

    Returns:
        例如 "TYZ6 Comdty" 或 "TYZ24 Comdty"。
    """
    month_code = MONTH_CODES[month - 1]
    yc = year_code(year, as_of)
    return f"{product_prefix}{month_code}{yc} Comdty"


def build_option_ticker(
    product_prefix: str,
    series: OptionSeries,
    option_type: str,
    strike: float,
) -> str:
    """
    构造 Bloomberg 期权 ticker。

    Args:
        product_prefix: 产品前缀，例如 "TY"。
        series: 期权系列信息。
        option_type: "P" 或 "C"。
        strike: 行权价。

    Returns:
        例如 "TYZ6P 110 Comdty" 或 "TYZ24P 110 Comdty"。
    """
    strike_str = str(int(strike)) if strike == int(strike) else f"{strike:g}"
    return f"{product_prefix}{series.key}{option_type} {strike_str} Comdty"


def list_option_series(
    start_date: dt.date,
    end_date: dt.date,
    future_ticker: str,
    expiry_overrides: Optional[Dict[str, str]] = None,
    as_of: dt.date | None = None,
) -> List[OptionSeries]:
    """
    列出回测区间内所有候选期权系列。

    范围从 start_date 前一个月覆盖到 end_date 后一个月，
    确保回测起始月与结束月都有可交易的期权系列。

    Args:
        start_date: 回测起始日期。
        end_date: 回测终止日期。
        future_ticker: 标的期货 ticker，用于推断交割月与年份。
        expiry_overrides: 手动覆盖到期日，格式 {"Z6": "2026-11-27"} 或 {"Z24": "2024-11-22"}。
        as_of: 参考日期，决定年份代码格式。

    Returns:
        OptionSeries 列表，按到期日升序排列。
    """
    overrides = expiry_overrides or {}
    future = parse_future_ticker(future_ticker, as_of)

    # 从 start_date 前一个月开始，到 end_date 后两个月，确保覆盖
    start_ym = (start_date.year, start_date.month - 1) if start_date.month > 1 \
        else (start_date.year - 1, 12)
    end_ym = (end_date.year, end_date.month + 2) if end_date.month <= 10 \
        else (end_date.year + 1, (end_date.month + 2) % 12 or 12)

    # 期货交割月不能超过标的期货交割月
    fut_ym = (future.delivery_year, future.delivery_month)
    if end_ym > fut_ym:
        end_ym = fut_ym

    series_list: List[OptionSeries] = []
    for y, m in month_iter(start_ym, end_ym):
        month_code = MONTH_CODES[m - 1]
        yc = year_code(y, as_of)
        key = f"{month_code}{yc}"
        if key in overrides:
            expiry = dt.date.fromisoformat(str(overrides[key]))
        else:
            py, pm = prev_month(y, m)
            expiry = fourth_friday(py, pm)
        series_list.append(
            OptionSeries(
                year=y,
                month=m,
                expiry_date=expiry,
                month_code=month_code,
                year_code=yc,
            )
        )

    return sorted(series_list, key=lambda s: s.expiry_date)


def nearest_expiry_series(
    series_list: List[OptionSeries],
    as_of: dt.date,
    min_days: int = 2,
) -> Optional[OptionSeries]:
    """从候选系列中选取 as_of 之后最近且满足最小剩余天数的系列。"""
    valid = [s for s in series_list if (s.expiry_date - as_of).days >= min_days]
    if not valid:
        return None
    return min(valid, key=lambda s: s.expiry_date)
