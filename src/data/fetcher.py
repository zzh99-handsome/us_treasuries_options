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
from src.data.option_ticker import build_option_ticker, list_option_series, parse_future_ticker


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

    @staticmethod
    def _normalize_daily_index(df: pd.DataFrame) -> pd.DataFrame:
        """统一 Bloomberg 日频索引为无时区、日历日精度。"""
        result = df.copy()
        index = pd.DatetimeIndex(pd.to_datetime(result.index))
        if index.tz is not None:
            # 日频 BDH 的索引是交易日标签；时区和盘中可得时间不由该标签表达。
            index = index.tz_localize(None)
        index = index.normalize()
        if index.has_duplicates:
            raise RuntimeError("日频数据标准化后出现重复交易日，请检查 Bloomberg 返回索引。")
        result.index = index
        return result.sort_index()

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
                return self._normalize_daily_index(
                    self._flatten_bdh(cached, ticker, fields)
                )

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
        df = self._normalize_daily_index(df)

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
                    result[tk] = self._normalize_daily_index(
                        self._flatten_bdh(df, tk, fields)
                    )
            else:
                need_fetch.append(tk)

        if need_fetch:
            self._check_connection()
            # 批量请求可明显减少历史期权的大量逐 ticker Bloomberg 往返。
            # _flatten_bdh 会把每个 ticker 从 xbbg MultiIndex 或长表结果中独立拆出。
            chunk_size = 80
            for offset in range(0, len(need_fetch), chunk_size):
                batch = need_fetch[offset : offset + chunk_size]
                try:
                    df = blp.bdh(
                        tickers=batch,
                        flds=fields,
                        start_date=start,
                        end_date=end,
                    )
                except Exception as batch_exc:
                    # 批请求失败时逐 ticker 回退，避免单个坏 security 丢掉整批有效数据。
                    print(f"[WARN] 期权批量请求失败，回退逐 ticker 拉取: {batch_exc}")
                    result.update(
                        self._fetch_option_batch_individually(
                            batch, start, end, fields, use_cache
                        )
                    )
                    continue
                if df is None:
                    continue
                df = self._to_pandas(df)
                if df.empty:
                    continue
                for tk in batch:
                    try:
                        ticker_df = self._flatten_bdh(df, tk, fields)
                        ticker_df = self._normalize_daily_index(ticker_df)
                    except RuntimeError as exc:
                        print(f"[WARN] 跳过期权 {tk}: {exc}")
                        continue
                    if use_cache:
                        self.cache.set(tk, fields, start, end, ticker_df)
                    result[tk] = ticker_df

        return result

    def _fetch_option_batch_individually(
        self,
        tickers: List[str],
        start: str,
        end: str,
        fields: List[str],
        use_cache: bool,
    ) -> Dict[str, pd.DataFrame]:
        """批量 BDH 请求不可用时逐 ticker 回退，返回可标准化的有效数据。"""
        result: Dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            try:
                raw = blp.bdh(
                    tickers=ticker,
                    flds=fields,
                    start_date=start,
                    end_date=end,
                )
                if raw is None:
                    continue
                raw = self._to_pandas(raw)
                if raw.empty:
                    continue
                normalized = self._flatten_bdh(raw, ticker, fields)
                normalized = self._normalize_daily_index(normalized)
                if use_cache:
                    self.cache.set(ticker, fields, start, end, normalized)
                result[ticker] = normalized
            except Exception as exc:
                print(f"[WARN] 跳过期权 {ticker}: {exc}")
        return result

    def fetch_option_chain_tickers(
        self,
        future_ticker: str,
        ref_date: dt.date,
        future_price: Optional[float] = None,
        option_type: str = "P",
        strike_step: float = 0.25,
        strike_half_width: float = 3.0,
        option_chain_source: str = "bloomberg_chain",
    ) -> List[str]:
        """
        获取某期货合约在参考日期附近的可交易期权 ticker 列表。

        优先尝试 Bloomberg CHAIN_TICKERS 接口；失败或不可用时，
        fallback 到按 future_price 为中心构造 strike 网格。

        Args:
            future_ticker: 期货合约 ticker。
            ref_date: 参考日期。
            future_price: 当前期货价格，用于 inferred fallback；若为 None 则不使用 fallback。
            option_type: 期权类型，默认 Put。
            strike_step: fallback 时 strike 网格步长。
            strike_half_width: fallback 时 strike 网格半宽。
            option_chain_source: "bloomberg_chain" 或 "inferred"。

        Returns:
            期权 ticker 列表。
        """
        if option_chain_source == "bloomberg_chain":
            try:
                self._check_connection()
                chain_df = blp.bdp(future_ticker, "CHAIN_TICKERS")
                if chain_df is not None:
                    chain_pd = self._to_pandas(chain_df)
                    if not chain_pd.empty:
                        # CHAIN_TICKERS 返回的 ticker 可能包含 call/put 混合，
                        # 按 option_type 过滤。
                        tickers = self._extract_option_tickers(chain_pd, option_type)
                        if tickers:
                            return tickers
            except Exception as exc:
                print(f"[WARN] Bloomberg CHAIN_TICKERS 获取失败，回退到 inferred: {exc}")

        if future_price is None:
            return []

        return self._infer_option_tickers(
            future_ticker,
            ref_date,
            future_price,
            option_type,
            strike_step,
            strike_half_width,
        )

    @staticmethod
    def _extract_option_tickers(chain_df: pd.DataFrame, option_type: str) -> List[str]:
        """从 Bloomberg CHAIN_TICKERS 返回中提取指定类型期权 ticker。"""
        # CHAIN_TICKERS 通常返回一列或多列包含期权 ticker。
        # 这里尝试找到包含 ticker 的列并过滤。
        tickers: set[str] = set()
        for col in chain_df.columns:
            for val in chain_df[col].dropna().astype(str).values:
                val = val.strip()
                if not val:
                    continue
                # 简单过滤：期权 ticker 中间字符为 P 或 C，如 TYZ6P 130 Comdty
                if option_type == "P" and "P " in val.replace("Comdty", "").strip():
                    tickers.add(val)
                elif option_type == "C" and "C " in val.replace("Comdty", "").strip():
                    tickers.add(val)
        return sorted(tickers)

    def _infer_option_tickers(
        self,
        future_ticker: str,
        ref_date: dt.date,
        future_price: float,
        option_type: str,
        strike_step: float,
        strike_half_width: float,
    ) -> List[str]:
        """按 strike 网格构造候选期权 ticker。"""
        series_list = list_option_series(
            start_date=ref_date,
            end_date=ref_date,
            future_ticker=future_ticker,
        )
        # 只取参考日期之后仍有效的最近 series
        series_list = [
            s for s in series_list
            if (s.expiry_date - ref_date).days >= 2
        ]
        if not series_list:
            return []

        center = round(future_price / strike_step) * strike_step
        n = int(round(strike_half_width / strike_step))
        strikes = [round(center + i * strike_step, 6) for i in range(-n, n + 1)]

        prefix = parse_future_ticker(future_ticker).prefix
        tickers: List[str] = []
        for series in series_list:
            for strike in strikes:
                tickers.append(
                    build_option_ticker(
                        product_prefix=prefix,
                        series=series,
                        option_type=option_type,
                        strike=strike,
                    )
                )
        return tickers

    def fetch_option_history_for_chain(
        self,
        future_ticker: str,
        ref_date: dt.date,
        start_date: dt.date | str,
        end_date: dt.date | str,
        fields: Optional[List[str]] = None,
        future_price: Optional[float] = None,
        option_chain_source: str = "bloomberg_chain",
        use_cache: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """
        为指定期货合约在参考日期构造/拉取期权链，并下载历史数据。

        Args:
            future_ticker: 期货合约 ticker。
            ref_date: 构造期权链的参考日期。
            start_date: 历史数据起始日期。
            end_date: 历史数据结束日期。
            fields: 期权字段列表。
            future_price: fallback 构造网格时用的期货价格。
            option_chain_source: 期权链来源。
            use_cache: 是否使用本地缓存。

        Returns:
            {ticker: DataFrame} 字典。
        """
        option_tickers = self.fetch_option_chain_tickers(
            future_ticker=future_ticker,
            ref_date=ref_date,
            future_price=future_price,
            option_chain_source=option_chain_source,
        )
        if not option_tickers:
            return {}
        return self.fetch_option_history(
            tickers=option_tickers,
            start_date=start_date,
            end_date=end_date,
            fields=fields,
            use_cache=use_cache,
        )

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
        # 某些 xbbg/数据适配器版本返回长表：ticker、date、field、value。
        # 先 pivot 成标准宽表，后续缓存和引擎只处理同一种 schema。
        column_map = {str(column).strip().lower(): column for column in df.columns}
        long_columns = {"ticker", "date", "field", "value"}
        if long_columns.issubset(column_map):
            ticker_col = column_map["ticker"]
            date_col = column_map["date"]
            field_col = column_map["field"]
            value_col = column_map["value"]
            rows = df[[ticker_col, date_col, field_col, value_col]].copy()
            ticker_values = rows[ticker_col].astype(str).str.strip().str.casefold()
            requested_ticker = ticker.strip().casefold()
            rows = rows.loc[ticker_values == requested_ticker]
            if rows.empty:
                available_tickers = sorted(ticker_values.unique().tolist())
                sample = available_tickers[:8]
                raise RuntimeError(
                    f"长表返回中找不到请求的 ticker {ticker}，"
                    f"批次内共 {len(available_tickers)} 个 ticker，示例：{sample}"
                )
            rows[date_col] = pd.to_datetime(rows[date_col], errors="coerce")
            rows[field_col] = rows[field_col].astype(str).str.strip().str.upper()
            requested_fields = [field.upper() for field in fields]
            rows = rows.loc[rows[field_col].isin(requested_fields)]
            if rows.empty:
                raise RuntimeError(
                    f"长表返回中 {ticker} 不包含请求字段 {fields}。"
                )
            if rows.duplicated([date_col, field_col]).any():
                raise RuntimeError(
                    f"长表返回中 {ticker} 存在重复 date/field 行，无法无损标准化。"
                )
            try:
                wide = rows.pivot(index=date_col, columns=field_col, values=value_col)
            except ValueError as exc:
                raise RuntimeError(f"无法将 {ticker} 长表行情转换为宽表。") from exc
            wide.columns.name = None
            wide = wide.rename(columns={field.upper(): field for field in fields})
            missing = [field for field in fields if field not in wide.columns]
            if missing:
                raise RuntimeError(
                    f"{ticker} 长表缺少字段 {missing}，可用列：{list(wide.columns)}"
                )
            return wide[fields]

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
