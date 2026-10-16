"""
回测组件构建器。

将数据下载、组件组装与回测引擎解耦，使 run_backtest.py 只负责 CLI 与配置。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.core.config_validator import ConfigValidator, resolve_ratio_grid
from src.core.engine import BacktestEngine
from src.core.strategy import MonthlyPutRatioSpreadStrategy
from src.core.windows import create_window
from src.data.fetcher import BloombergDataFetcher
from src.data.futures_calendar import active_contracts_in_range, front_month_ticker
from src.optimization.grid_search import GridSearch
from src.timing.bull_bear import BullBearRegime


class BacktestBuilder:
    """
    根据配置构建完整回测引擎。

    职责：
    - 校验配置；
    - 下载期货与期权历史数据；
    - 组装 strategy / window / grid_search / engine；
    - 返回可直接运行的 BacktestEngine。
    """

    def __init__(self, config: dict) -> None:
        """
        Args:
            config: 已通过 CLI 覆盖与预处理后的 effective 配置字典。
        """
        self.config = config
        self.fetcher = BloombergDataFetcher(Path(config.get("cache_dir", "./cache")))

    def build(self) -> BacktestEngine:
        """构建并返回 BacktestEngine。"""
        ConfigValidator.validate(self.config)

        futures_data = self._download_futures()
        options_data = self._download_options(futures_data)
        continuous_futures = self._build_continuous_futures_series(futures_data)

        strategy = self._create_strategy()
        window = self._create_window()
        grid_search = self._create_grid_search()
        timing_factor = self._create_timing_factor(continuous_futures)

        return BacktestEngine(
            futures_data=futures_data,
            options_data=options_data,
            strategy=strategy,
            window=window,
            grid_search=grid_search,
            config=self.config,
            timing_factor=timing_factor,
        )

    def _download_futures(self) -> Dict[str, pd.DataFrame]:
        """下载回测所需的所有近月期货合约数据。"""
        start_date = dt.date.fromisoformat(self.config["data_start_date"])
        end_date = dt.date.fromisoformat(self.config["end_date"])
        product_code = self.config["product_code"]
        roll_on_first_day = self.config.get("roll_on_first_day_of_delivery_month", True)

        active_contracts = active_contracts_in_range(
            start_date=start_date,
            end_date=end_date,
            product_prefix=product_code,
            roll_on_first_day_of_delivery_month=roll_on_first_day,
        )

        futures_fields = list(self.config.get("futures_fields", ["PX_LAST"]))
        settlement_field = self.config.get("settlement_field", "PX_LAST")
        if settlement_field not in futures_fields:
            futures_fields.append(settlement_field)

        futures_data: Dict[str, pd.DataFrame] = {}
        for ticker in active_contracts:
            try:
                df = self.fetcher.fetch_futures_history(
                    ticker=ticker,
                    start_date=start_date,
                    end_date=end_date,
                    fields=futures_fields,
                )
                futures_data[ticker] = df
            except RuntimeError as exc:
                print(f"[WARN] 跳过合约 {ticker}: {exc}")

        if not futures_data:
            raise RuntimeError("无有效期货合约数据，请检查 Bloomberg 连接与合约代码。")

        return futures_data

    def _download_options(self, futures_data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """按每月实际可交易期权链按需下载期权历史数据。"""
        start_date = dt.date.fromisoformat(self.config["data_start_date"])
        end_date = dt.date.fromisoformat(self.config["end_date"])
        product_code = self.config["product_code"]
        roll_on_first_day = self.config.get("roll_on_first_day_of_delivery_month", True)
        option_fields = list(self.config.get("option_fields", ["PX_BID", "PX_ASK", "PX_LAST"]))
        option_chain_source = self.config.get("data", {}).get(
            "option_chain_source", "bloomberg_chain"
        )

        all_dates = pd.DatetimeIndex([], tz=None)
        for df in futures_data.values():
            all_dates = all_dates.union(df.index)
        all_dates = all_dates.sort_values()

        options_data: Dict[str, pd.DataFrame] = {}
        handled_months: set[tuple[int, int]] = set()

        for ts in all_dates:
            decision_date = ts.date()
            month_key = (decision_date.year, decision_date.month)
            if month_key in handled_months:
                continue

            future_ticker = front_month_ticker(
                decision_date, product_code, roll_on_first_day
            )
            future_df = futures_data.get(future_ticker)
            if future_df is None or ts not in future_df.index:
                continue

            future_price = float(future_df.loc[ts, "PX_LAST"])
            if pd.isna(future_price):
                continue

            handled_months.add(month_key)

            chain_data = self.fetcher.fetch_option_history_for_chain(
                future_ticker=future_ticker,
                ref_date=decision_date,
                start_date=start_date,
                end_date=end_date,
                fields=option_fields,
                future_price=future_price,
                option_chain_source=option_chain_source,
            )
            options_data.update(chain_data)

        return options_data

    def _build_continuous_futures_series(
        self,
        futures_data: Dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """将近月合约价格拼接为连续价格序列，用于择时因子。"""
        product_code = self.config["product_code"]
        roll_on_first_day = self.config.get("roll_on_first_day_of_delivery_month", True)

        all_dates = pd.DatetimeIndex([], tz=None)
        for df in futures_data.values():
            all_dates = all_dates.union(df.index)
        all_dates = all_dates.sort_values()

        values = []
        for ts in all_dates:
            ticker = front_month_ticker(
                ts.date(), product_code, roll_on_first_day
            )
            df = futures_data.get(ticker)
            if df is not None and ts in df.index:
                values.append(float(df.loc[ts, "PX_LAST"]))
            else:
                values.append(None)

        return pd.DataFrame({"PX_LAST": values}, index=all_dates).dropna()

    def _create_strategy(self) -> MonthlyPutRatioSpreadStrategy:
        """创建策略实例。"""
        return MonthlyPutRatioSpreadStrategy(
            strike_step=self.config["strike_step"],
            strike_half_width=self.config["strike_half_width"],
            contracts_k2=(
                self.config.get("contracts_per_leg", 1)
                * self.config["ratio_quantity_scale"]
            ),
            product_prefix=self.config["product_prefix"],
            point_value=self.config.get("point_value", 1000.0),
            max_bid_ask_spread_64=self.config.get("execution", {}).get(
                "max_bid_ask_spread_64", 12.0
            ),
        )

    def _create_window(self):
        """创建训练窗口。"""
        return create_window(
            window_type=self.config["window_type"],
            train_months=self.config["train_months"],
            start_date=dt.date.fromisoformat(self.config["data_start_date"]),
        )

    def _create_grid_search(self) -> GridSearch:
        """创建网格搜索器。"""
        return GridSearch(
            intervals=self.config["intervals"],
            ratios=resolve_ratio_grid(self.config),
            strike_step=self.config["strike_step"],
            strike_half_width=self.config["strike_half_width"],
            contracts_k2=(
                self.config.get("contracts_per_leg", 1)
                * self.config["ratio_quantity_scale"]
            ),
            product_prefix=self.config["product_prefix"],
            point_value=self.config.get("point_value", 1000.0),
            max_bid_ask_spread_64=self.config.get("execution", {}).get(
                "max_bid_ask_spread_64", 12.0
            ),
        )

    def _create_timing_factor(
        self,
        continuous_futures: pd.DataFrame,
    ) -> Optional[BullBearRegime]:
        """可选：创建并拟合择时因子。"""
        if not self.config.get("enable_timing_factor", False):
            return None
        timing_factor = BullBearRegime()
        timing_factor.fit({"futures": continuous_futures})
        return timing_factor
