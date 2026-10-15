"""
回测引擎测试。
"""

from __future__ import annotations

import datetime as dt
from typing import Dict

import numpy as np
import pandas as pd
import pytest

from src.core.engine import BacktestEngine
from src.core.strategy import MonthlyPutRatioSpreadStrategy
from src.core.windows import RollingWindow
from src.data.futures_calendar import active_contracts_in_range
from src.data.option_ticker import build_option_ticker, list_option_series
from src.optimization.grid_search import GridSearch


def _make_synthetic_data() -> tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    """构造 2020 年合成数据，包含多个近月期货合约及其期权。"""
    dates = pd.date_range(start="2020-01-01", end="2020-12-31", freq="B")
    n = len(dates)
    # 期货价格缓慢下跌，制造熊市环境
    prices = 130.0 - (130.0 - 120.0) * np.arange(n) / n

    # 生成 4 个季度合约（2020 年的 H/M/U/Z）。
    # 由于 2020 年相对今天已到期，Bloomberg 使用 2 位年份代码。
    contracts = {
        "TYH20 Comdty": pd.DataFrame({"PX_LAST": prices}, index=dates),
        "TYM20 Comdty": pd.DataFrame({"PX_LAST": prices}, index=dates),
        "TYU20 Comdty": pd.DataFrame({"PX_LAST": prices}, index=dates),
        "TYZ20 Comdty": pd.DataFrame({"PX_LAST": prices}, index=dates),
    }

    # 期权系列：覆盖整个 2020 年
    series_list = list_option_series(
        start_date=dt.date(2020, 1, 1),
        end_date=dt.date(2020, 12, 31),
        future_ticker="TYZ20 Comdty",
    )

    # 固定行权价网格，覆盖 120~130 区间
    center = 125.0
    strikes = [round(center + i * 0.25, 6) for i in range(-20, 21)]

    options_data: Dict[str, pd.DataFrame] = {}
    for series in series_list:
        for s in strikes:
            ticker = build_option_ticker("TY", series, "P", s)
            intrinsic = max(0.0, (center - s)) * 64.0
            bid = intrinsic + 1.0
            ask = intrinsic + 2.0
            df = pd.DataFrame(
                index=dates,
                columns=["PX_BID", "PX_ASK", "PX_LAST"],
                dtype=float,
            )
            df.loc[:, "PX_BID"] = bid
            df.loc[:, "PX_ASK"] = ask
            df.loc[:, "PX_LAST"] = (bid + ask) / 2
            options_data[ticker] = df

    return contracts, options_data


@pytest.fixture
def engine(sample_config: dict) -> BacktestEngine:
    """构造完整回测引擎。"""
    futures_data, options_data = _make_synthetic_data()
    config = sample_config.copy()
    config["start_date"] = "2020-01-01"
    config["end_date"] = "2020-12-31"

    strategy = MonthlyPutRatioSpreadStrategy(
        strike_step=config["strike_step"],
        strike_half_width=config["strike_half_width"],
        contracts_k2=config["contracts_per_leg"],
        product_prefix=config["product_prefix"],
        point_value=config["point_value"],
    )
    window = RollingWindow(train_months=config["train_months"])
    grid_search = GridSearch(
        intervals=config["intervals"],
        ratios=config["ratios"],
        strike_step=config["strike_step"],
        strike_half_width=config["strike_half_width"],
        contracts_k2=config["contracts_per_leg"],
        product_prefix=config["product_prefix"],
        point_value=config["point_value"],
    )

    return BacktestEngine(
        futures_data=futures_data,
        options_data=options_data,
        strategy=strategy,
        window=window,
        grid_search=grid_search,
        config=config,
    )


def test_engine_runs_and_produces_equity_curve(engine: BacktestEngine) -> None:
    """引擎应能运行并生成净值曲线。"""
    result = engine.run(dt.date(2020, 4, 1), dt.date(2020, 12, 31))
    assert not result.equity_curve.empty
    assert len(result.trades) > 0
    assert len(result.trade_pnls) == len(result.trades)
    assert len(result.monthly_params) > 0


def test_engine_no_lookahead(engine: BacktestEngine) -> None:
    """
    验证无 lookahead：每月参数必须仅由该月之前的历史数据决定。

    具体做法：比较正常回测与截断回测，
    截断回测在某个月之后停止，之前月份的参数应一致。
    """
    result_full = engine.run(dt.date(2020, 4, 1), dt.date(2020, 12, 31))
    result_partial = engine.run(dt.date(2020, 4, 1), dt.date(2020, 9, 30))

    params_full = {d: p for d, p in result_full.monthly_params}
    params_partial = {d: p for d, p in result_partial.monthly_params}

    for d, p in params_partial.items():
        assert d in params_full, f"{d} 应在完整回测中出现"
        assert params_full[d] == p, f"{d} 的参数不应因未来数据而改变"


def test_engine_trades_held_to_expiry(engine: BacktestEngine) -> None:
    """每笔 trade 的 exit_date 应为其对应期权到期日。"""
    result = engine.run(dt.date(2020, 4, 1), dt.date(2020, 12, 31))
    for trade in result.trades:
        assert trade.exit_date == trade.spread.expiry_date
        assert trade.exit_date >= trade.entry_date


def test_engine_selects_front_month_contract(engine: BacktestEngine) -> None:
    """引擎应根据调仓日选择对应的近月合约。"""
    # 2020-04-01 处于 Q2，近月合约应为 TYM20（Jun 2020）
    ticker = engine._active_future_ticker(dt.date(2020, 4, 1))
    assert ticker == "TYM20 Comdty"

    # 2020-10-01 处于 Q4，近月合约应为 TYZ20（Dec 2020）
    ticker = engine._active_future_ticker(dt.date(2020, 10, 1))
    assert ticker == "TYZ20 Comdty"
