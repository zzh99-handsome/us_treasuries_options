"""
数据层测试：ticker 构造、缓存、fetcher 接口。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from src.data import fetcher as fetcher_module
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
        month_code="Z", year_code="4",
    )
    ticker_expired = build_option_ticker("TY", series_expired, "P", 110.0)
    assert ticker_expired == "TYZ4P 110 Comdty"

    ticker_decimal = build_option_ticker("TY", series, "P", 110.5)
    assert ticker_decimal == "TYZ6P 110.5 Comdty"


def test_historical_option_ticker_uses_one_digit_year_code() -> None:
    """Bloomberg 历史期权系列仍使用 1 位年份码（终端样例：TYZ7P）。"""
    series = list_option_series(
        start_date=dt.date(2017, 11, 1),
        end_date=dt.date(2017, 12, 31),
        future_ticker="TYZ17 Comdty",
        as_of=dt.date(2026, 10, 16),
    )
    december_2017 = next(s for s in series if s.year == 2017 and s.month == 12)

    assert december_2017.year_code == "7"
    assert build_option_ticker("TY", december_2017, "P", 125.0) == "TYZ7P 125 Comdty"


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


def test_fetcher_normalizes_daily_timestamp_index() -> None:
    """日频行情的时间索引统一到无时区的交易日标签。"""
    source = pd.DataFrame(
        {"PX_LAST": [100.0, 101.0]},
        index=pd.date_range("2020-01-01 16:00", periods=2, freq="D", tz="America/New_York"),
    )
    result = BloombergDataFetcher._normalize_daily_index(source)
    assert result.index.equals(pd.DatetimeIndex(["2020-01-01", "2020-01-02"]))
    assert result.index.tz is None


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


def test_flatten_bdh_long_format_futures() -> None:
    """bdh 长表应按 ticker/field 过滤并转为期货字段宽表。"""
    df = pd.DataFrame(
        {
            "ticker": ["TYH24 Comdty", "TYH24 Comdty", "TYM24 Comdty"],
            "date": ["2024-01-02", "2024-01-03", "2024-01-02"],
            "field": ["PX_LAST", "PX_LAST", "PX_LAST"],
            "value": [110.25, 110.50, 111.00],
        }
    )

    result = BloombergDataFetcher._flatten_bdh(
        df, "TYH24 Comdty", ["PX_LAST"]
    )

    assert list(result.columns) == ["PX_LAST"]
    assert result.loc[pd.Timestamp("2024-01-02"), "PX_LAST"] == 110.25
    assert result.loc[pd.Timestamp("2024-01-03"), "PX_LAST"] == 110.50


def test_flatten_bdh_long_format_options() -> None:
    """期权长表应同时还原 Bid/Ask，并忽略请求之外的 ticker 和字段。"""
    df = pd.DataFrame(
        {
            "ticker": [
                "TYH24P 110 Comdty",
                "TYH24P 110 Comdty",
                "TYH24P 110 Comdty",
                "TYH24P 111 Comdty",
            ],
            "date": ["2024-01-02"] * 4,
            "field": ["PX_BID", "PX_ASK", "PX_LAST", "PX_BID"],
            "value": [0.125, 0.25, 0.1875, 0.50],
        }
    )

    result = BloombergDataFetcher._flatten_bdh(
        df, "TYH24P 110 Comdty", ["PX_BID", "PX_ASK"]
    )

    assert list(result.columns) == ["PX_BID", "PX_ASK"]
    assert result.iloc[0].to_dict() == {"PX_BID": 0.125, "PX_ASK": 0.25}


def test_flatten_bdh_long_format_reports_ticker_and_missing_field() -> None:
    """长表缺少目标 ticker 或请求字段时应报告具体原因。"""
    df = pd.DataFrame(
        {
            "ticker": ["TYH24 Comdty"],
            "date": ["2024-01-02"],
            "field": ["PX_BID"],
            "value": [110.0],
        }
    )

    with pytest.raises(RuntimeError, match="ticker"):
        BloombergDataFetcher._flatten_bdh(df, "TYM24 Comdty", ["PX_BID"])
    with pytest.raises(RuntimeError, match="PX_ASK"):
        BloombergDataFetcher._flatten_bdh(df, "TYH24 Comdty", ["PX_BID", "PX_ASK"])


def test_fetch_futures_history_accepts_bloomberg_long_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """期货拉取入口应把 Bloomberg ticker/date/field/value 长表标准化。"""
    source = pd.DataFrame(
        {
            "ticker": ["TYH24 Comdty", "TYH24 Comdty"],
            "date": ["2024-01-02", "2024-01-03"],
            "field": ["PX_LAST", "PX_LAST"],
            "value": [110.25, 110.50],
        }
    )
    monkeypatch.setattr(BloombergDataFetcher, "_check_connection", lambda self: None)
    monkeypatch.setattr(fetcher_module.blp, "bdh", lambda **kwargs: source)

    result = BloombergDataFetcher(tmp_path).fetch_futures_history(
        "TYH24 Comdty", "2024-01-01", "2024-01-05", use_cache=False
    )

    assert list(result.columns) == ["PX_LAST"]
    assert result.index.equals(pd.DatetimeIndex(["2024-01-02", "2024-01-03"]))
    assert result["PX_LAST"].tolist() == [110.25, 110.50]


def test_fetch_option_history_batches_and_splits_long_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """期权批量 BDH 长表应按 ticker 拆分并返回标准字段表。"""
    tickers = ["TYH24P 110 Comdty", "TYH24P 111 Comdty"]
    source = pd.DataFrame(
        {
            "ticker": [tickers[0], tickers[0], tickers[1], tickers[1]],
            "date": ["2024-01-02"] * 4,
            "field": ["PX_BID", "PX_ASK", "PX_BID", "PX_ASK"],
            "value": [0.125, 0.25, 0.25, 0.375],
        }
    )
    requested_batches: list[list[str]] = []

    def fake_bdh(*, tickers, **kwargs):
        requested_batches.append(tickers)
        return source

    monkeypatch.setattr(BloombergDataFetcher, "_check_connection", lambda self: None)
    monkeypatch.setattr(fetcher_module.blp, "bdh", fake_bdh)

    result = BloombergDataFetcher(tmp_path).fetch_option_history(
        tickers, "2024-01-01", "2024-01-05", fields=["PX_BID", "PX_ASK"], use_cache=False
    )

    assert requested_batches == [tickers]
    assert set(result) == set(tickers)
    assert result[tickers[0]].iloc[0].to_dict() == {"PX_BID": 0.125, "PX_ASK": 0.25}
    assert result[tickers[1]].iloc[0].to_dict() == {"PX_BID": 0.25, "PX_ASK": 0.375}
