"""
回测引擎。

按月遍历再平衡日，在历史训练窗口上搜索最优参数，
然后以该参数构建 Put Ratio Spread 并持有至到期。

每个调仓日动态选择近月期货合约及其对应期权。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
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
    window_diagnostics: List[Dict] = field(default_factory=list)
    skipped_trades: List[Dict] = field(default_factory=list)

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

        execution = config.get("execution", {})
        configured_spread_limit = execution.get(
            "max_bid_ask_spread_64",
            getattr(strategy, "max_bid_ask_spread_64", None),
        )
        if configured_spread_limit is not None and float(configured_spread_limit) < 0:
            raise ValueError("execution.max_bid_ask_spread_64 must be non-negative")
        strategy.max_bid_ask_spread_64 = (
            None if configured_spread_limit is None else float(configured_spread_limit)
        )
        grid_search.max_bid_ask_spread_64 = strategy.max_bid_ask_spread_64
        if strategy.contracts_k2 != grid_search.contracts_k2:
            raise ValueError("strategy and grid_search must use the same buy-leg contract quantity")
        if execution.get("buy_option_price", "ask") != "ask":
            raise ValueError("当前执行模型仅支持买入腿使用 Ask")
        if execution.get("sell_option_price", "bid") != "bid":
            raise ValueError("当前执行模型仅支持卖出腿使用 Bid")
        if execution.get("invalid_quote_policy", "skip_trade") != "skip_trade":
            raise ValueError("当前仅支持 invalid_quote_policy=skip_trade")
        backtest_options = config.get("backtest", {})
        if backtest_options.get("hold_to_expiry", True) is not True:
            raise ValueError("当前策略只支持持有至到期")
        if backtest_options.get("roll_existing_positions", False):
            raise ValueError("存续组合不能移仓；请设置 roll_existing_positions=false")
        strategy_options = config.get("strategy", {})
        if strategy_options.get("require_same_expiry", True) is not True:
            raise ValueError("同一组合的所有期权腿必须使用相同到期日")
        if config.get("constraints", {}).get("enabled", False):
            raise ValueError("资本/保证金约束尚未实现，constraints.enabled 必须为 false")

        self.product_code = config["product_code"]
        self.product_prefix = config["product_prefix"]
        self.option_type = config["option_type"]
        self.strike_step = config["strike_step"]
        self.strike_half_width = config["strike_half_width"]
        self.point_value = config.get("point_value", 1000.0)
        self.settlement_field = config.get("settlement_field", "PX_LAST")
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
        self.series_by_future: Dict[str, List[OptionSeries]] = {}
        for fut_ticker in self.futures_data.keys():
            contract_series = list_option_series(
                start_date=start,
                end_date=end,
                future_ticker=fut_ticker,
                expiry_overrides=config.get("expiry_overrides"),
            )
            self.series_by_future[fut_ticker] = contract_series
            self.series_list.extend(contract_series)
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

    def _future_price(
        self,
        d: dt.date,
        ticker: Optional[str] = None,
        field: str = "PX_LAST",
    ) -> Optional[float]:
        """精确读取指定合约/日期/字段，不做前向填充。"""
        if ticker is None:
            ticker = self._active_future_ticker(d)
        if ticker is None or ticker not in self.futures_data:
            return None
        df = self.futures_data[ticker]
        try:
            value = float(df.loc[pd.Timestamp(d), field])
            return value if pd.notna(value) else None
        except (KeyError, TypeError, ValueError):
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
        return None if not math.isfinite(f) else f

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
            chain[round(s, 6)] = {
                "bid": bid,
                "ask": ask,
                "ticker": ticker,
                "quote_date": d,
            }
        return chain

    def _series_candidates(self, future_ticker: Optional[str], d: dt.date) -> List[OptionSeries]:
        """Return matching future option expiries after entry, nearest first."""
        return sorted(
            (
                series
                for series in self.series_by_future.get(future_ticker or "", [])
                if series.expiry_date > d
            ),
            key=lambda series: series.expiry_date,
        )

    def _has_any_liquid_candidate(
        self,
        entry_date: dt.date,
        expiry_date: dt.date,
        future_price: float,
        chain: Dict[float, Dict[str, Optional[float]]],
    ) -> bool:
        """Check whether at least one configured candidate has executable legs."""
        for interval in self.grid_search.intervals:
            for ratio in self.grid_search.ratios:
                if self.strategy.build_trade(
                    rebalance_date=entry_date,
                    expiry_date=expiry_date,
                    future_price=future_price,
                    option_chain=chain,
                    interval=interval,
                    ratio=ratio,
                ) is not None:
                    return True
        return False

    def _rebalance_dates(self, start: dt.date, end: dt.date) -> List[dt.date]:
        """生成每月第一个交易日的日期列表。"""
        rebals: List[dt.date] = []
        current_month = None
        for ts in self.all_dates:
            d = ts.date()
            if d < start or d > end:
                continue
            active_ticker = self._active_future_ticker(d)
            if self._future_price(d, active_ticker) is None:
                continue
            month_key = (d.year, d.month)
            if month_key != current_month:
                rebals.append(d)
                current_month = month_key
        return rebals

    def _build_episodes(
        self,
        decision_date: dt.date,
        train_start: dt.date,
        train_end: dt.date,
    ) -> List[Episode]:
        """
        根据训练窗口内的再平衡日期构建历史 episode。

        只使用 train_end 及之前的 episode，避免 lookahead。
        """
        historical_dates = self._rebalance_dates(self.all_dates[0].date(), train_end)
        exclusions = {
            "not_matured_before_decision": 0,
            "missing_entry_or_settlement_price": 0,
            "no_liquid_same_expiry_bid_ask_pair": 0,
        }
        episodes: List[Episode] = []
        for rd in historical_dates:
            if rd < train_start or rd >= decision_date:
                continue
            future_ticker = self._active_future_ticker(rd)
            future_entry = self._future_price(rd, future_ticker)
            if future_entry is None:
                exclusions["missing_entry_or_settlement_price"] += 1
                continue
            selected = None
            saw_matured_series = False
            for series in self._series_candidates(future_ticker, rd):
                if series.expiry_date >= decision_date:
                    continue
                saw_matured_series = True
                # Historical episodes enter training only after exact expiry
                # settlement is known; never fill this value from another date.
                future_expiry = self._future_price(
                    series.expiry_date, future_ticker, self.settlement_field
                )
                if future_expiry is None:
                    continue
                chain = self._option_chain_for_series(series, rd)
                if not self._has_any_liquid_candidate(
                    rd, series.expiry_date, future_entry, chain
                ):
                    continue
                selected = (series, future_expiry, chain)
                break
            if selected is None:
                if saw_matured_series:
                    exclusions["no_liquid_same_expiry_bid_ask_pair"] += 1
                else:
                    exclusions["not_matured_before_decision"] += 1
                continue
            series, future_expiry, chain = selected
            episodes.append(
                Episode(
                    entry_date=rd,
                    expiry_date=series.expiry_date,
                    future_price_entry=future_entry,
                    future_price_expiry=future_expiry,
                    option_chain=chain,
                    future_ticker=future_ticker or "",
                    regime=(self.timing_factor.regime(rd) if self.timing_factor else "neutral"),
                )
            )
        self._last_episode_exclusions = exclusions
        return episodes

    def run(self, start_date: dt.date, end_date: dt.date) -> BacktestResult:
        """
        执行回测。

        Args:
            start_date: 回测起始日期。
            end_date: 回测终止日期。

        Returns:
            BacktestResult。
        """
        if end_date < start_date:
            raise ValueError("end_date 必须不早于 start_date")
        if len(self.all_dates) == 0:
            raise ValueError("没有可用期货交易日")

        rebals = self._rebalance_dates(start_date, end_date)
        if not rebals:
            raise ValueError("回测区间内无有效再平衡日期")

        trades: List[Trade] = []
        monthly_params: List[Tuple[dt.date, ParameterSet]] = []
        grid_history: List[Tuple[dt.date, GridSearchResult]] = []
        diagnostics: List[Dict] = []
        skipped_trades: List[Dict] = []
        oos_dates = self.all_dates[
            (self.all_dates >= pd.Timestamp(start_date))
            & (self.all_dates <= pd.Timestamp(end_date))
        ]
        if len(oos_dates) < 2:
            raise ValueError("样本外区间至少需要两个有效交易日才能生成绩效报告")
        daily_realized_pnl = pd.Series(0.0, index=oos_dates)
        optimization_config = self.config.get("optimization", {})
        objective_name = optimization_config.get("objective", "cumulative_pnl")
        if objective_name != "cumulative_pnl":
            raise ValueError(f"当前仅实现 optimization.objective=cumulative_pnl，收到 {objective_name!r}")
        minimum_samples = int(optimization_config.get("minimum_samples", 1))
        regime_aware = bool(optimization_config.get("regime_aware_ratio", False))

        for rd in rebals:
            train_end = self.window.get_train_end(rd)
            train_start = self.window.get_train_start(rd)
            train_episodes = self._build_episodes(rd, train_start, train_end)
            current_regime = self.timing_factor.regime(rd) if self.timing_factor else "neutral"
            regime_excluded = 0
            if regime_aware and self.timing_factor is not None:
                regime_filtered = [ep for ep in train_episodes if ep.regime == current_regime]
                regime_excluded = len(train_episodes) - len(regime_filtered)
                train_episodes = regime_filtered

            search_result = self.grid_search.search(train_episodes) if train_episodes else None
            fallback_reason = None
            if search_result is not None:
                grid_history.append((rd, search_result))
            effective_count = (
                search_result.valid_counts.get(search_result.best_params, 0)
                if search_result is not None
                else 0
            )
            if search_result is None:
                fallback_reason = "no_matured_training_episodes"
            elif effective_count < minimum_samples:
                fallback_reason = "below_minimum_samples"
            elif not pd.notna(search_result.best_score) or search_result.best_score == -float("inf"):
                fallback_reason = "no_candidate_with_valid_quotes"

            if fallback_reason:
                intervals = self.config.get("intervals", [0.5])
                ratios = self.config.get("ratios", [1.0])
                params = ParameterSet(interval=intervals[0], ratio=ratios[0])
            else:
                params = search_result.best_params
            monthly_params.append((rd, params))

            # 建仓
            trade_id = f"{rd:%Y%m}-01"
            diagnostic = {
                "window_id": f"W-{rd:%Y%m%d}",
                "decision_date": rd.isoformat(),
                "training_start": train_start.isoformat(),
                "training_end": train_end.isoformat(),
                "information_cutoff": train_end.isoformat(),
                "candidate_months": len(
                    self._rebalance_dates(train_start, train_end)
                ),
                "total_training_episodes": len(train_episodes),
                "regime_sample_count": len(train_episodes),
                "effective_samples": effective_count,
                "excluded_samples": len(self._rebalance_dates(train_start, train_end)) - len(train_episodes),
                "exclusion_reasons": {
                    **getattr(self, "_last_episode_exclusions", {}),
                    "regime_mismatch": regime_excluded,
                },
                "objective": objective_name,
                "regime": current_regime,
                "regime_aware": regime_aware,
                "fallback": fallback_reason,
                "selected_interval": params.interval,
                "selected_ratio": params.ratio,
                "oos_trade_id": trade_id,
                "candidate_scores": [],
            }
            if search_result is not None:
                ranked = search_result.ranked_scores
                diagnostic["candidate_scores"] = [
                    {
                        "rank": rank,
                        "interval": candidate.interval,
                        "ratio": candidate.ratio,
                        "score_usd": score if pd.notna(score) and score != -float("inf") else None,
                        "valid_samples": search_result.valid_counts.get(candidate, 0),
                    }
                    for rank, (candidate, score) in enumerate(ranked, start=1)
                ]
                diagnostic["best_score_usd"] = (
                    ranked[0][1] if ranked and pd.notna(ranked[0][1]) and ranked[0][1] != -float("inf") else None
                )
                diagnostic["second_best_score_usd"] = (
                    ranked[1][1] if len(ranked) > 1 and pd.notna(ranked[1][1]) and ranked[1][1] != -float("inf") else None
                )
                if diagnostic["best_score_usd"] is not None and diagnostic["second_best_score_usd"] is not None:
                    diagnostic["score_gap_usd"] = diagnostic["best_score_usd"] - diagnostic["second_best_score_usd"]
                    diagnostic["relative_score_gap"] = (
                        diagnostic["score_gap_usd"] / max(abs(diagnostic["best_score_usd"]), 1e-12)
                    )
                    stability_threshold = float(optimization_config.get("near_tie_relative_gap", 0.02))
                    diagnostic["parameter_instability_warning"] = (
                        diagnostic["relative_score_gap"] <= stability_threshold
                    )
            diagnostics.append(diagnostic)

            active_ticker = self._active_future_ticker(rd)
            series_candidates = self._series_candidates(active_ticker, rd)
            if not series_candidates:
                skipped_trades.append({"trade_id": trade_id, "entry_date": rd.isoformat(), "reason": "no_matching_option_expiry"})
                continue
            future_entry = self._future_price(rd, active_ticker)
            if future_entry is None:
                skipped_trades.append({"trade_id": trade_id, "entry_date": rd.isoformat(), "reason": "missing_entry_future_price"})
                continue
            trade = None
            series = None
            for candidate_series in series_candidates:
                chain = self._option_chain_for_series(candidate_series, rd)
                candidate_trade = self.strategy.build_trade(
                    rebalance_date=rd,
                    expiry_date=candidate_series.expiry_date,
                    future_price=future_entry,
                    option_chain=chain,
                    interval=params.interval,
                    ratio=params.ratio,
                )
                if candidate_trade is not None:
                    trade = candidate_trade
                    series = candidate_series
                    break
            if trade is None:
                skipped_trades.append({
                    "trade_id": trade_id,
                    "entry_date": rd.isoformat(),
                    "expiry_date": ";".join(s.expiry_date.isoformat() for s in series_candidates),
                    "reason": (
                        "no_liquid_same_expiry_bid_ask_pair across eligible expiries; "
                        "requires positive uncrossed two-sided quotes and configured max spread"
                    ),
                })
                continue
            trade.trade_id = trade_id
            trade.future_ticker = active_ticker or ""
            trade.regime = current_regime
            trade.window_id = diagnostic["window_id"]
            trades.append(trade)
            diagnostic["expiry_date"] = series.expiry_date.isoformat()
            diagnostic["quote_timestamp_semantics"] = "daily date index; intraday availability unverified"

        # 仅在实际结算日确认总盈亏；未到期或缺少结算价的交易保持 open。
        trade_pnls: List[float] = []
        for trade in trades:
            settlement_price = (
                self._future_price(
                    trade.exit_date, trade.future_ticker, self.settlement_field
                )
                if trade.exit_date <= end_date
                else None
            )
            if settlement_price is None:
                trade.status = "open"
                continue
            trade.status = "settled"
            trade.settlement_price = settlement_price
            trade.settlement_cashflow = trade.spread.settlement_cashflow(settlement_price)
            trade.realized_pnl = trade.spread.realized_pnl(settlement_price)
            trade_pnls.append(trade.realized_pnl)
            settlement_ts = pd.Timestamp(trade.exit_date)
            if settlement_ts in daily_realized_pnl.index:
                daily_realized_pnl.loc[settlement_ts] += trade.realized_pnl

        equity = self.initial_capital + daily_realized_pnl.cumsum()

        return BacktestResult(
            config=self.config,
            equity_curve=equity,
            trades=trades,
            trade_pnls=trade_pnls,
            monthly_params=monthly_params,
            grid_search_history=grid_history,
            window_diagnostics=diagnostics,
            skipped_trades=skipped_trades,
        )
