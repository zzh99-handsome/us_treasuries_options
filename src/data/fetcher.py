"""
Bloomberg 数据下载接口。

提供期货历史行情、期权历史行情及截面数据的统一拉取接口，
所有接口均支持自定义 start_date / end_date，并内置本地缓存。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from xbbg import blp

from src.data.cache import DataCache


class BloombergDataFetcher:
    """
    Bloomberg 数据拉取器。

    Attributes:
        cache: DataCache 实例，用于本地缓存。
    """

    def __init__(self, cache_dir: Path | str) -> None:
        """
        Args:
            cache_dir: 本地缓存目录。
        """
        self.cache = DataCache(cache_dir)

    @staticmethod
    def _to_iso(d: dt.date | str) -> str:
        """统一转换为 ISO 日期字符串。"""
        if isinstance(d, str):
            return d
        return d.isoformat()

    @staticmethod
    def _to_pandas(df) -> pd.DataFrame:
        """
        将 xbbg 返回的 narwhals DataFrame 转换为 pandas DataFrame。

        若已经是 pandas，则直接返回副本。
        """
        if df is None:
            raise ValueError("输入 DataFrame 为 None")
        if hasattr(df, "to_pandas") and callable(getattr(df, "to_pandas")):
            return df.to_pandas()
        return df.copy()

    def _check_connection(self) -> None:
        """检查 Bloomberg 终端连接状态，未连接则抛出 RuntimeError。"""
        try:
            test = blp.bdp("IBM US Equity", "NAME")
        except Exception as exc:
            raise RuntimeError("Bloomberg 连接失败，请确认终端已登录。") from exc

        if test is None:
            raise RuntimeError("Bloomberg 连接失败，请确认终端已登录。")
        test_pd = self._to_pandas(test)
        if test_pd.empty:
            raise RuntimeError("Bloomberg 连接失败，请确认终端已登录。")

    def fetch_futures_history(
        self,
        ticker: str,
        start_date: dt.date | str,
        end_date: dt.date | str,
        fields: Optional[List[str]] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        下载期货历史行情。

        Args:
            ticker: 期货 ticker，例如 "TYZ6 Comdty"。
            start_date: 起始日期。
            end_date: 终止日期。
            fields: 需要拉取的字段列表，默认 ["PX_LAST"]。
            use_cache: 是否使用本地缓存。

        Returns:
            以日期为索引、字段为列的 DataFrame。
        """
        fields = fields or ["PX_LAST"]
        start = self._to_iso(start_date)
        end = self._to_iso(end_date)

        if use_cache:
            cached = self.cache.get(ticker, fields, start, end)
            if cached is not None:
                return cached

        self._check_connection()

        try:
            df = blp.bdh(
                tickers=ticker,
                flds=fields,
                start_date=start,
                end_date=end,
            )
        except Exception as exc:
            raise RuntimeError(f"下载期货 {ticker} 历史数据失败: {exc}") from exc

        if df is None:
            raise RuntimeError(f"期货 {ticker} 在 {start} ~ {end} 无数据。")

        # 转换为 pandas（xbbg 可能返回 narwhals DataFrame）
        df = self._to_pandas(df)
        if df.empty:
            raise RuntimeError(f"期货 {ticker} 在 {start} ~ {end} 无数据。")

        # xbbg bdh 返回的列可能是 MultiIndex；拍平并校验字段
        df = self._flatten_bdh(df, ticker, fields)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        if use_cache:
            self.cache.set(ticker, fields, start, end, df)
        return df

    def fetch_option_history(
        self,
        tickers: List[str],
        start_date: dt.date | str,
        end_date: dt.date | str,
        fields: Optional[List[str]] = None,
        use_cache: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """
        批量下载期权历史行情。

        Args:
            tickers: 期权 ticker 列表。
            start_date: 起始日期。
            end_date: 终止日期。
            fields: 字段列表，默认 ["PX_BID", "PX_ASK", "PX_LAST"]。
            use_cache: 是否使用本地缓存。

        Returns:
            {ticker: DataFrame} 字典。无数据或失败的 ticker 不会出现在结果中。
        """
        fields = fields or ["PX_BID", "PX_ASK", "PX_LAST"]
        start = self._to_iso(start_date)
        end = self._to_iso(end_date)
        result: Dict[str, pd.DataFrame] = {}

        need_fetch: List[str] = []
        for tk in tickers:
            if use_cache and self.cache.exists(tk, fields, start, end):
                df = self.cache.get(tk, fields, start, end)
                if df is not None and not df.empty:
                    result[tk] = df
            else:
                need_fetch.append(tk)

        if need_fetch:
            self._check_connection()
            for tk in need_fetch:
                try:
                    df = blp.bdh(
                        tickers=tk,
                        flds=fields,
                        start_date=start,
                        end_date=end,
                    )
                except Exception:
                    # 单个 ticker 失败不影响其他
                    continue
                if df is None:
                    continue
                df = self._to_pandas(df)
                if df.empty:
                    continue
                try:
                    df = self._flatten_bdh(df, tk, fields)
                except RuntimeError as exc:
                    print(f"[WARN] {exc}")
                    continue
                df.index = pd.to_datetime(df.index)
                df = df.sort_index()
                if use_cache:
                    self.cache.set(tk, fields, start, end, df)
                result[tk] = df

        return result

    def fetch_bulk(
        self,
        tickers: List[str],
        fields: List[str],
        ref_date: dt.date | str,
    ) -> Dict[str, Dict[str, Optional[float]]]:
        """
        下载某一天的截面数据。

        Args:
            tickers: ticker 列表。
            fields: 字段列表。
            ref_date: 参考日期。

        Returns:
            {ticker: {field: value}}。失败的 ticker 不会出现在结果中。
        """
        self._check_connection()
        ref = self._to_iso(ref_date)

        result: Dict[str, Dict[str, Optional[float]]] = {}
        chunk = 80
        for i in range(0, len(tickers), chunk):
            batch = tickers[i : i + chunk]
            try:
                df = blp.bdp(batch, fields, DVD_START=ref, DVD_END=ref)
            except Exception:
                continue
            if df is None:
                continue
            df = self._to_pandas(df)
            if df.empty:
                continue
            for t in batch:
                if t not in df.index:
                    continue
                row = df.loc[t]
                result[t] = {f: self._to_float(row.get(f)) for f in fields}
        return result

    @staticmethod
    def _to_float(val) -> Optional[float]:
        """将 Bloomberg 返回值安全转换为 float，None/NaN 返回 None。"""
        if val is None:
            return None
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        import math

        return None if math.isnan(f) else f

    @staticmethod
    def _flatten_bdh(df: pd.DataFrame, ticker: str, fields: List[str]) -> pd.DataFrame:
        """将 xbbg 返回的 bdh DataFrame 拍平为以字段为列的格式。"""
        # xbbg bdh 可能返回列 MultiIndex，顺序可能是 (ticker, field) 或 (field, ticker)
        if isinstance(df.columns, pd.MultiIndex):
            # 尝试找到 ticker 所在的层级
            cols_ticker_first = [c for c in df.columns if c[0] == ticker]
            cols_field_first = [c for c in df.columns if c[1] == ticker]

            if cols_ticker_first:
                sub = df[cols_ticker_first].copy()
                sub.columns = [c[1] for c in sub.columns]
            elif cols_field_first:
                sub = df[cols_field_first].copy()
                sub.columns = [c[0] for c in sub.columns]
            else:
                raise RuntimeError(f"无法在返回的列中找到 {ticker}：{list(df.columns)}")

            # 确保所需字段都存在
            missing = [f for f in fields if f not in sub.columns]
            if missing:
                raise RuntimeError(f"{ticker} 缺少字段 {missing}，可用列：{list(sub.columns)}")
            return sub[fields]

        # 单列索引：检查字段是否存在
        missing = [f for f in fields if f not in df.columns]
        if missing:
            raise RuntimeError(f"{ticker} 缺少字段 {missing}，可用列：{list(df.columns)}")
        return df[fields].copy()
