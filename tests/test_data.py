"""
数据层测试：ticker 构造、缓存、fetcher 接口。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from src.data.cache import DataCache
from src.data.fetcher import BloombergDataFetcher
from src.data.option_ticker import (
    OptionSeries,
    build_option_ticker,
    fourth_friday,
    list_option_series,
    parse_future_ticker,
)


def test_fourth_friday_known_date() -> None:
    """已知 2020-11 的第 4 个周五是 2020-11-27。"""
    assert fourth_friday(2020, 11) == dt.date(2020, 11, 27)


def test_parse_future_ticker_typical() -> None:
    """解析活跃期货代码 TYZ6 Comdty。"""
    info = parse_future_ticker("TYZ6 Comdty", dt.date(2026, 10, 15))
    assert info.prefix == "TY"
    assert info.month_code == "Z"
    assert info.year_code == "6"
    assert info.delivery_month == 12
    assert info.delivery_year == 2026


def test_parse_future_ticker_expired() -> None:
    """解析已到期期货代码 TYZ24 Comdty。"""
    info = parse_future_ticker("TYZ24 Comdty")
    assert info.prefix == "TY"
    assert info.month_code == "Z"
    assert info.year_code == "24"
    assert info.delivery_month == 12
    assert info.delivery_year == 2024


def test_build_option_ticker() -> None:
    """构造期权 ticker。"""
    series = OptionSeries(
        year=2026, month=12, expiry_date=dt.date(2026, 11, 27),
        month_code="Z", year_code="6",
    )
    ticker = build_option_ticker("TY", series, "P", 110.0)
    assert ticker == "TYZ6P 110 Comdty"

    series_expired = OptionSeries(
        year=2024, month=12, expiry_date=dt.date(2024, 11, 22),
        month_code="Z", year_code="24",
    )
    ticker_expired = build_option_ticker("TY", series_expired, "P", 110.0)
    assert ticker_expired == "TYZ24P 110 Comdty"

    ticker_decimal = build_option_ticker("TY", series, "P", 110.5)
    assert ticker_decimal == "TYZ6P 110.5 Comdty"


def test_list_option_series_sorted() -> None:
    """列出的期权系列应按到期日升序排列。"""
    series = list_option_series(
        start_date=dt.date(2020, 1, 1),
        end_date=dt.date(2020, 6, 30),
        future_ticker="TYH0 Comdty",
    )
    assert len(series) >= 3
    expiry_dates = [s.expiry_date for s in series]
    assert expiry_dates == sorted(expiry_dates)


def test_cache_roundtrip(tmp_path: Path) -> None:
    """缓存写入与读取。"""
    cache = DataCache(tmp_path)
    df = pd.DataFrame({"PX_LAST": [100.0, 101.0, 102.0]}, index=pd.date_range("2020-01-01", periods=3))
    cache.set("TYZ6 Comdty", ["PX_LAST"], "2020-01-01", "2020-01-03", df)

    assert cache.exists("TYZ6 Comdty", ["PX_LAST"], "2020-01-01", "2020-01-03")
    loaded = cache.get("TYZ6 Comdty", ["PX_LAST"], "2020-01-01", "2020-01-03")
    assert loaded is not None
    pd.testing.assert_frame_equal(loaded, df)


def test_cache_clear(tmp_path: Path) -> None:
    """清空缓存后文件应被删除。"""
    cache = DataCache(tmp_path)
    df = pd.DataFrame({"PX_LAST": [100.0]})
    cache.set("TYZ6 Comdty", ["PX_LAST"], "2020-01-01", "2020-01-01", df)
    cache.clear()
    assert len(list(tmp_path.glob("*.pkl"))) == 0


def test_fetcher_to_pandas_converts_narwhals() -> None:
    """_to_pandas 应将 narwhals-like 对象转换为 pandas DataFrame。"""
    df_pandas = pd.DataFrame({"PX_LAST": [100.0, 101.0]}, index=pd.date_range("2020-01-01", periods=2))

    class FakeNarwhals:
        def to_pandas(self):
            return df_pandas.copy()

    result = BloombergDataFetcher._to_pandas(FakeNarwhals())
    pd.testing.assert_frame_equal(result, df_pandas)


def test_fetcher_to_pandas_passes_through_pandas() -> None:
    """_to_pandas 对 pandas DataFrame 应直接返回副本。"""
    df = pd.DataFrame({"PX_LAST": [100.0]})
    result = BloombergDataFetcher._to_pandas(df)
    pd.testing.assert_frame_equal(result, df)
    assert result is not df


def test_flatten_bdh_single_level() -> None:
    """_flatten_bdh 处理单列索引 DataFrame。"""
    df = pd.DataFrame({"PX_LAST": [100.0, 101.0]}, index=pd.date_range("2020-01-01", periods=2))
    result = BloombergDataFetcher._flatten_bdh(df, "TYH24 Comdty", ["PX_LAST"])
    assert list(result.columns) == ["PX_LAST"]


def test_flatten_bdh_multiindex_ticker_first() -> None:
    """_flatten_bdh 处理 (ticker, field) 顺序的 MultiIndex。"""
    arrays = [["TYH24 Comdty", "TYH24 Comdty"], ["PX_LAST", "PX_BID"]]
    df = pd.DataFrame(
        [[100.0, 99.0], [101.0, 100.0]],
        index=pd.date_range("2020-01-01", periods=2),
        columns=pd.MultiIndex.from_arrays(arrays),
    )
    result = BloombergDataFetcher._flatten_bdh(df, "TYH24 Comdty", ["PX_LAST"])
    assert list(result.columns) == ["PX_LAST"]
    assert len(result) == 2


def test_flatten_bdh_multiindex_field_first() -> None:
    """_flatten_bdh 处理 (field, ticker) 顺序的 MultiIndex。"""
    arrays = [["PX_LAST", "PX_BID"], ["TYH24 Comdty", "TYH24 Comdty"]]
    df = pd.DataFrame(
        [[100.0, 99.0], [101.0, 100.0]],
        index=pd.date_range("2020-01-01", periods=2),
        columns=pd.MultiIndex.from_arrays(arrays),
    )
    result = BloombergDataFetcher._flatten_bdh(df, "TYH24 Comdty", ["PX_LAST"])
    assert list(result.columns) == ["PX_LAST"]


def test_flatten_bdh_missing_field_raises() -> None:
    """_flatten_bdh 在缺少字段时应抛出 RuntimeError。"""
    df = pd.DataFrame({"PX_BID": [100.0]}, index=pd.date_range("2020-01-01", periods=1))
    with pytest.raises(RuntimeError):
        BloombergDataFetcher._flatten_bdh(df, "TYH24 Comdty", ["PX_LAST"])
