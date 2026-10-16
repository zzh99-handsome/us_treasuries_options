"""
合成数据端到端演示。

不依赖 Bloomberg，用模拟数据跑完整回测流程并生成 HTML 报告。
每个调仓日动态选择近月期货合约。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

from src.core.engine import BacktestEngine
from src.core.strategy import MonthlyPutRatioSpreadStrategy
from src.core.windows import RollingWindow
from src.data.option_ticker import build_option_ticker, list_option_series
from src.metrics.performance import calculate_metrics
from src.optimization.grid_search import GridSearch
from src.report.reporter import render_report


def make_synthetic_data() -> tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    """构造 2020 年合成期货与期权数据，包含多个近月合约。"""
    dates = pd.date_range("2020-01-01", "2020-12-31", freq="B")
    n = len(dates)
    # 熊市：价格从 130 缓慢下跌到 120
    prices = 130.0 - (130.0 - 120.0) * np.arange(n) / n

    futures_data = {
        "TYH20 Comdty": pd.DataFrame({"PX_LAST": prices}, index=dates),
        "TYM20 Comdty": pd.DataFrame({"PX_LAST": prices}, index=dates),
        "TYU20 Comdty": pd.DataFrame({"PX_LAST": prices}, index=dates),
        "TYZ20 Comdty": pd.DataFrame({"PX_LAST": prices}, index=dates),
        "TYH21 Comdty": pd.DataFrame({"PX_LAST": prices}, index=dates),
    }

    series_list = list_option_series(
        dt.date(2020, 1, 1),
        dt.date(2020, 12, 31),
        "TYH21 Comdty",
    )
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

    return futures_data, options_data


def main() -> None:
    """运行合成数据回测并生成报告。"""
    config = {
        "product_code": "TY",
        "product_prefix": "TY",
        "roll_on_first_day_of_delivery_month": True,
        "option_type": "P",
        "strike_step": 0.25,
        "strike_half_width": 3.0,
        "intervals": [0.25, 0.5, 0.75, 1.0],
        "ratios": [round(i / 10, 1) for i in range(5, 26)],
        "ratio_quantity_scale": 10,
        "window_type": "rolling",
        "train_months": 3,
        "initial_capital": 1_000_000.0,
        "point_value": 1000.0,
        "settlement_field": "PX_LAST",
        "contracts_per_leg": 1,
        "enable_timing_factor": False,
        "expiry_overrides": {},
        "data_source": "synthetic",
        "data_start_date": "2020-01-01",
        "data_fetched_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "optimization": {
            "objective": "cumulative_pnl",
            "minimum_samples": 2,
            "regime_aware_ratio": False,
            "near_tie_relative_gap": 0.02,
        },
        "execution": {
            "buy_option_price": "ask",
            "sell_option_price": "bid",
            "invalid_quote_policy": "skip_trade",
            "max_bid_ask_spread_64": 12,
        },
        "constraints": {"enabled": False},
        "market_specifications": {
            "verification_status": "synthetic_unverified",
            "notes": "Synthetic prices validate code paths only; not market performance.",
        },
    }

    futures_data, options_data = make_synthetic_data()

    strategy = MonthlyPutRatioSpreadStrategy(
        strike_step=config["strike_step"],
        strike_half_width=config["strike_half_width"],
        contracts_k2=config["contracts_per_leg"] * config["ratio_quantity_scale"],
        product_prefix=config["product_prefix"],
        point_value=config["point_value"],
        max_bid_ask_spread_64=config["execution"]["max_bid_ask_spread_64"],
    )
    window = RollingWindow(train_months=config["train_months"])
    grid_search = GridSearch(
        intervals=config["intervals"],
        ratios=config["ratios"],
        strike_step=config["strike_step"],
        strike_half_width=config["strike_half_width"],
        contracts_k2=config["contracts_per_leg"] * config["ratio_quantity_scale"],
        product_prefix=config["product_prefix"],
        point_value=config["point_value"],
        max_bid_ask_spread_64=config["execution"]["max_bid_ask_spread_64"],
    )

    engine = BacktestEngine(
        futures_data=futures_data,
        options_data=options_data,
        strategy=strategy,
        window=window,
        grid_search=grid_search,
        config=config,
    )

    result = engine.run(dt.date(2020, 4, 1), dt.date(2020, 12, 31))
    metrics = calculate_metrics(
        result.equity_curve,
        trade_pnls=result.trade_pnls,
        denominator_info={"type": "initial_capital", "value": config["initial_capital"]},
    )

    report_path = Path("./reports/demo_synthetic.html")
    render_report(result, metrics, report_path)

    print("\n========== Synthetic Backtest Result ==========")
    for k, v in metrics.to_dict().items():
        print(f"  {k}: {v}")
    print(
        f"\n[INFO] Settled {len(result.trade_pnls)}, open "
        f"{sum(trade.status == 'open' for trade in result.trades)}, "
        f"skipped {len(result.skipped_trades)}"
    )
    print(f"[OK] Report generated: {report_path.resolve()}")


if __name__ == "__main__":
    main()
