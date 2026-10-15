"""
回测引擎。

按月遍历再平衡日，在历史训练窗口上搜索最优参数，
然后以该参数构建 Put Ratio Spread 并持有至到期。

每个调仓日动态选择近月期货合约及其对应期权。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.core.instrument import PutRatioSpread
from src.core.strategy import MonthlyPutRatioSpreadStrategy, Trade
from src.core.windows import TrainingWindow
from src.data.futures_calendar import active_contracts_in_range, front_month_ticker
from src.data.option_ticker import (
    OptionSeries,
    build_option_ticker,
    list_option_series,
    nearest_expiry_series,
)
from src.optimization.grid_search import Episode, GridSearch, GridSearchResult, ParameterSet
from src.timing.base import TimingFactor


@dataclass
class BacktestResult:
    """回测结果。"""

    config: Dict
    equity_curve: pd.Series
    trades: List[Trade]
    trade_pnls: List[float]
    monthly_params: List[Tuple[dt.date, ParameterSet]]
    grid_search_history: List[Tuple[dt.date, GridSearchResult]]

    @property
    def start_date(self) -> dt.date:
        return self.equity_curve.index[0].date()

    @property
    def end_date(self) -> dt.date:
        return self.equity_curve.index[-1].date()


class BacktestEngine:
    """
    Put Ratio Spread 回测引擎。

    数据约定：
    - futures_data: {期货 ticker: DataFrame}，每个 DataFrame 以日期为索引，列 "PX_LAST"。
    - options_data: {期权 ticker: DataFrame}，以日期为索引，列 "PX_BID"/"PX_ASK"/"PX_LAST"。

    引擎会在每个调仓日根据 `product_code` 动态选择近月期货合约。
    """

    def __init__(
        self,
        futures_data: Dict[str, pd.DataFrame],
        options_data: Dict[str, pd.DataFrame],
        strategy: MonthlyPutRatioSpreadStrategy,
        window: TrainingWindow,
        grid_search: GridSearch,
        config: Dict,
        timing_factor: Optional[TimingFactor] = None,
    ) -> None:
        """
        Args:
            futures_data: 多个期货合约的日价格字典。
            options_data: 期权日价格字典。
            strategy: 策略实例。
            window: 训练窗口。
            grid_search: 网格搜索器。
            config: 回测配置字典。
            timing_factor: 可选的择时因子。
        """
        self.futures_data = {
            k: v.sort_index() for k, v in futures_data.items() if v is not None and not v.empty
        }
        self.options_data = options_data
        self.strategy = strategy
        self.window = window
        self.grid_search = grid_search
        self.config = config
        self.timing_factor = timing_factor

        self.product_code = config["product_code"]
        self.product_prefix = config["product_prefix"]
        self.option_type = config["option_type"]
        self.strike_step = config["strike_step"]
        self.strike_half_width = config["strike_half_width"]
        self.point_value = config.get("point_value", 1000.0)
        self.initial_capital = config.get("initial_capital", 1_000_000.0)
        self.roll_on_first_day = config.get("roll_on_first_day_of_delivery_month", True)

        # 汇总所有合约的交易日，作为全局日期索引
        all_dates: pd.DatetimeIndex = pd.DatetimeIndex([], tz=None)
        for df in self.futures_data.values():
            all_dates = all_dates.union(df.index)
        self.all_dates = all_dates.sort_values()

        # 预计算回测期内所有可能用到的期权系列
        start = self.all_dates[0].date() if len(self.all_dates) else dt.date.today()
        end = self.all_dates[-1].date() if len(self.all_dates) else dt.date.today()
        self.series_list: List[OptionSeries] = []
        for fut_ticker in self.futures_data.keys():
            self.series_list.extend(
                list_option_series(
                    start_date=start,
                    end_date=end,
                    future_ticker=fut_ticker,
                    expiry_overrides=config.get("expiry_overrides"),
                )
            )
        # 去重并按到期日排序
        seen = set()
        unique: List[OptionSeries] = []
        for s in sorted(self.series_list, key=lambda x: x.expiry_date):
            if s.key not in seen:
                seen.add(s.key)
                unique.append(s)
        self.series_list = unique

    def _active_future_ticker(self, d: dt.date) -> Optional[str]:
        """返回 d 对应的近月期货 ticker。"""
        return front_month_ticker(d, self.product_code, self.roll_on_first_day)

    def _future_price(self, d: dt.date, ticker: Optional[str] = None) -> Optional[float]:
        """获取指定合约某日期货收盘价。"""
        if ticker is None:
            ticker = self._active_future_ticker(d)
        if ticker is None or ticker not in self.futures_data:
            return None
        df = self.futures_data[ticker]
        try:
            return float(df.loc[pd.Timestamp(d), "PX_LAST"])
        except KeyError:
            return None

    def _option_price(
        self,
        ticker: str,
        d: dt.date,
        field: str,
    ) -> Optional[float]:
        """获取某日期权某字段价格。"""
        df = self.options_data.get(ticker)
        if df is None or df.empty:
            return None
        try:
            val = df.loc[pd.Timestamp(d), field]
        except KeyError:
            return None
        if val is None:
            return None
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        import math

        return None if math.isnan(f) else f

    def _build_strike_grid(self, future_price: float) -> List[float]:
        """以期货价为中心构建行权价网格。"""
        center = round(future_price / self.strike_step) * self.strike_step
        n = int(round(self.strike_half_width / self.strike_step))
        return [round(center + i * self.strike_step, 6) for i in range(-n, n + 1)]

    def _option_chain_for_series(
        self,
        series: OptionSeries,
        d: dt.date,
    ) -> Dict[float, Dict[str, Optional[float]]]:
        """为某系列在某日期构建期权链。"""
        future_price = self._future_price(d)
        if future_price is None:
            return {}
        strikes = self._build_strike_grid(future_price)
        chain: Dict[float, Dict[str, Optional[float]]] = {}
        for s in strikes:
            ticker = build_option_ticker(self.product_prefix, series, self.option_type, s)
            bid = self._option_price(ticker, d, "PX_BID")
            ask = self._option_price(ticker, d, "PX_ASK")
            chain[round(s, 6)] = {"bid": bid, "ask": ask}
        return chain

    def _rebalance_dates(self, start: dt.date, end: dt.date) -> List[dt.date]:
        """生成每月第一个交易日的日期列表。"""
        rebals: List[dt.date] = []
        current_month = None
        for ts in self.all_dates:
            d = ts.date()
            if d < start or d > end:
                continue
            if d.month != current_month:
                rebals.append(d)
                current_month = d.month
        return rebals

    def _build_episodes(
        self,
        rebalance_dates: List[dt.date],
        train_end: dt.date,
    ) -> List[Episode]:
        """
        根据训练窗口内的再平衡日期构建历史 episode。

        只使用 train_end 及之前的 episode，避免 lookahead。
        """
        episodes: List[Episode] = []
        for rd in rebalance_dates:
            if rd > train_end:
                continue
            series = nearest_expiry_series(self.series_list, rd)
            if series is None:
                continue
            future_entry = self._future_price(rd)
            future_expiry = self._future_price(series.expiry_date)
            if future_entry is None or future_expiry is None:
                continue
            chain = self._option_chain_for_series(series, rd)
            if not chain:
                continue
            episodes.append(
                Episode(
                    entry_date=rd,
                    expiry_date=series.expiry_date,
                    future_price_entry=future_entry,
                    future_price_expiry=future_expiry,
                    option_chain=chain,
                )
            )
        return episodes

    def _select_params(
        self,
        rebalance_date: dt.date,
        episodes: List[Episode],
    ) -> ParameterSet:
        """根据训练 episode 选择最优参数。"""
        if self.timing_factor is not None:
            regime = self.timing_factor.regime(rebalance_date)
            if regime == "bear":
                episodes = [
                    ep for ep in episodes
                    if ep.future_price_expiry < ep.future_price_entry
                ]
            elif regime == "bull":
                episodes = [
                    ep for ep in episodes
                    if ep.future_price_expiry >= ep.future_price_entry
                ]

        if not episodes:
            intervals = self.config.get("intervals", [0.5])
            ratios = self.config.get("ratios", [1.5])
            return ParameterSet(interval=intervals[0], ratio=ratios[0])

        result = self.grid_search.search(episodes)
        return result.best_params

    def run(self, start_date: dt.date, end_date: dt.date) -> BacktestResult:
        """
        执行回测。

        Args:
            start_date: 回测起始日期。
            end_date: 回测终止日期。

        Returns:
            BacktestResult。
        """
        rebals = self._rebalance_dates(start_date, end_date)
        if not rebals:
            raise ValueError("回测区间内无有效再平衡日期")

        trades: List[Trade] = []
        monthly_params: List[Tuple[dt.date, ParameterSet]] = []
        grid_history: List[Tuple[dt.date, GridSearchResult]] = []

        # 追踪每笔 trade 的每日盈亏，索引为全局交易日
        daily_pnl = pd.Series(0.0, index=self.all_dates)

        for rd in rebals:
            train_end = self.window.get_train_end(rd)
            train_episodes = self._build_episodes(rebals, train_end)

            params = self._select_params(rd, train_episodes)
            monthly_params.append((rd, params))

            if train_episodes:
                result = self.grid_search.search(train_episodes)
                grid_history.append((rd, result))

            # 建仓
            series = nearest_expiry_series(self.series_list, rd)
            if series is None:
                continue
            future_entry = self._future_price(rd)
            if future_entry is None:
                continue
            chain = self._option_chain_for_series(series, rd)
            trade = self.strategy.build_trade(
                rebalance_date=rd,
                expiry_date=series.expiry_date,
                future_price=future_entry,
                option_chain=chain,
                interval=params.interval,
                ratio=params.ratio,
            )
            if trade is None:
                continue
            trades.append(trade)

            # 计算该 trade 在持有期内的每日 mark-to-market P&L
            # 使用建仓日对应的近月合约价格序列
            active_ticker = self._active_future_ticker(rd)
            if active_ticker is None or active_ticker not in self.futures_data:
                continue
            fut_df = self.futures_data[active_ticker]
            mask = (fut_df.index >= pd.Timestamp(rd)) & (
                fut_df.index <= pd.Timestamp(series.expiry_date)
            )
            prices = fut_df.loc[mask, "PX_LAST"]
            pnl_series = trade.spread.daily_pnl_series(prices)
            daily_pnl.loc[pnl_series.index] += pnl_series

        # 将累计 P&L 转为净值曲线
        equity = self.initial_capital + daily_pnl.cumsum()

        # 计算每笔 trade 的实现盈亏
        trade_pnls = []
        for t in trades:
            future_expiry = self._future_price(t.exit_date)
            if future_expiry is None:
                # 若到期日无价格，使用最后已知价格
                active_ticker = self._active_future_ticker(t.exit_date)
                if active_ticker and active_ticker in self.futures_data:
                    future_expiry = self.futures_data[active_ticker]["PX_LAST"].asof(
                        pd.Timestamp(t.exit_date)
                    )
            if future_expiry is not None:
                pnl = t.spread.realized_pnl(float(future_expiry))
                trade_pnls.append(pnl)

        return BacktestResult(
            config=self.config,
            equity_curve=equity,
            trades=trades,
            trade_pnls=trade_pnls,
            monthly_params=monthly_params,
            grid_search_history=grid_history,
        )
