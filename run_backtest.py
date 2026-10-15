"""
Put Ratio Spread 回测入口脚本。

每个调仓日动态选择近月期货合约及其对应期权。

用法：
    cd backtest/
    python run_backtest.py --config config.yaml
    python run_backtest.py --start-date 2020-01-01 --end-date 2023-01-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd
import yaml

from src.core.engine import BacktestEngine
from src.core.strategy import MonthlyPutRatioSpreadStrategy
from src.core.windows import create_window
from src.data.fetcher import BloombergDataFetcher
from src.data.futures_calendar import active_contracts_in_range
from src.data.option_ticker import (
    OptionSeries,
    build_option_ticker,
    list_option_series,
)
from src.metrics.performance import calculate_metrics
from src.optimization.grid_search import GridSearch
from src.report.reporter import render_report
from src.timing.bull_bear import BullBearRegime


def load_config(path: Path) -> dict:
    """加载 YAML 配置。"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_all_option_tickers(
    futures_data: Dict[str, pd.DataFrame],
    config: dict,
) -> List[str]:
    """
    根据所有期货合约价格历史构建可能用到的期权 ticker。

    使用所有合约价格的最小/最大值，按 strike_half_width 扩展后生成行权价网格。
    """
    if not futures_data:
        raise RuntimeError("futures_data 为空，无法构建行权价网格。")

    # 校验每个合约都有 PX_LAST 列
    for ticker, df in futures_data.items():
        if "PX_LAST" not in df.columns:
            raise RuntimeError(
                f"期货合约 {ticker} 缺少 PX_LAST 列，可用列：{list(df.columns)}"
            )

    all_prices = pd.concat([df["PX_LAST"] for df in futures_data.values()])
    if all_prices.empty:
        raise RuntimeError("所有期货合约的 PX_LAST 均为空。")

    min_px = float(all_prices.min())
    max_px = float(all_prices.max())
    step = config["strike_step"]
    half_width = config["strike_half_width"]

    # 以最小和最大值为中心，各向外扩展 half_width
    low_center = round(min_px / step) * step
    high_center = round(max_px / step) * step
    n = int(round(half_width / step))

    strikes: set = set()
    for center in (low_center, high_center):
        for i in range(-n, n + 1):
            strikes.add(round(center + i * step, 6))

    # 为每个合约生成对应的期权系列
    start = min(df.index[0].date() for df in futures_data.values())
    end = max(df.index[-1].date() for df in futures_data.values())
    all_series: List[OptionSeries] = []
    for fut_ticker in futures_data.keys():
        all_series.extend(
            list_option_series(
                start_date=start,
                end_date=end,
                future_ticker=fut_ticker,
                expiry_overrides=config.get("expiry_overrides"),
            )
        )

    seen_series = set()
    unique_series: List[OptionSeries] = []
    for s in sorted(all_series, key=lambda x: x.expiry_date):
        if s.key not in seen_series:
            seen_series.add(s.key)
            unique_series.append(s)

    tickers: List[str] = []
    option_type = config["option_type"]
    prefix = config["product_prefix"]
    for series in unique_series:
        for s in strikes:
            tickers.append(build_option_ticker(prefix, series, option_type, s))
    return tickers


def build_continuous_futures_series(futures_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    将近月合约价格拼接为一条连续价格序列，用于择时因子拟合。

    在每个交易日使用当天对应的近月合约价格。
    """
    from src.data.futures_calendar import front_month_ticker

    all_dates = pd.DatetimeIndex([], tz=None)
    for df in futures_data.values():
        all_dates = all_dates.union(df.index)
    all_dates = all_dates.sort_values()

    values = []
    for ts in all_dates:
        d = ts.date()
        ticker = front_month_ticker(d, "TY", roll_on_first_day_of_delivery_month=True)
        df = futures_data.get(ticker)
        if df is not None and ts in df.index:
            values.append(float(df.loc[ts, "PX_LAST"]))
        else:
            values.append(None)

    return pd.DataFrame({"PX_LAST": values}, index=all_dates).dropna()


def run_backtest(config: dict, cli_args: argparse.Namespace | None = None) -> Path:
    """
    执行完整回测流程。

    Args:
        config: 配置字典。
        cli_args: 命令行参数，可用于覆盖配置。

    Returns:
        生成的 HTML 报告路径。
    """
    start_date = dt.date.fromisoformat(
        cli_args.start_date if cli_args and cli_args.start_date else config["start_date"]
    )
    end_date = dt.date.fromisoformat(
        cli_args.end_date if cli_args and cli_args.end_date else config["end_date"]
    )
    window_type = (
        cli_args.window_type if cli_args and cli_args.window_type else config["window_type"]
    )
    train_months = (
        cli_args.train_months if cli_args and cli_args.train_months else config["train_months"]
    )

    cache_dir = Path(config.get("cache_dir", "./cache"))
    report_dir = Path(config.get("report_dir", "./reports"))
    report_dir.mkdir(parents=True, exist_ok=True)

    product_code = config["product_code"]
    print(f"[INFO] 回测区间: {start_date} ~ {end_date}")
    print(f"[INFO] 标的期货产品: {product_code}（动态选择近月合约）")

    fetcher = BloombergDataFetcher(cache_dir)

    # 1. 确定回测期内所有近月合约并下载
    print("[INFO] 确定近月合约列表 ...")
    active_contracts = active_contracts_in_range(
        start_date=start_date,
        end_date=end_date,
        product_prefix=product_code,
        roll_on_first_day_of_delivery_month=config.get(
            "roll_on_first_day_of_delivery_month", True
        ),
    )
    print(f"[INFO] 共 {len(active_contracts)} 个近月合约: {', '.join(active_contracts)}")

    print("[INFO] 下载期货历史数据 ...")
    futures_data: Dict[str, pd.DataFrame] = {}
    for ticker in active_contracts:
        try:
            df = fetcher.fetch_futures_history(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                fields=config.get("futures_fields", ["PX_LAST"]),
            )
            futures_data[ticker] = df
        except RuntimeError as exc:
            print(f"[WARN] 跳过合约 {ticker}: {exc}")

    if not futures_data:
        raise RuntimeError("无有效期货合约数据，请检查 Bloomberg 连接与合约代码。")

    print(f"[INFO] 成功加载 {len(futures_data)} 个合约:")
    for t, df in futures_data.items():
        print(f"       {t}: {len(df)} 行，列 {list(df.columns)}")

    # 2. 确定期权系列并构造所有 ticker
    print("[INFO] 构造期权 ticker 列表 ...")
    option_tickers = build_all_option_tickers(futures_data, config)
    print(f"[INFO] 共 {len(option_tickers)} 个期权 ticker 待下载")

    # 3. 下载期权历史数据
    print("[INFO] 下载期权历史数据 ...")
    options_data = fetcher.fetch_option_history(
        tickers=option_tickers,
        start_date=start_date,
        end_date=end_date,
        fields=config.get("option_fields", ["PX_BID", "PX_ASK", "PX_LAST"]),
    )
    print(f"[INFO] 成功下载 {len(options_data)} 个期权 ticker 数据")

    # 4. 初始化组件
    strategy = MonthlyPutRatioSpreadStrategy(
        strike_step=config["strike_step"],
        strike_half_width=config["strike_half_width"],
        contracts_k2=config.get("contracts_per_leg", 1),
        product_prefix=config["product_prefix"],
        point_value=config.get("point_value", 1000.0),
    )

    window = create_window(
        window_type=window_type,
        train_months=train_months,
        start_date=start_date,
    )

    grid_search = GridSearch(
        intervals=config["intervals"],
        ratios=config["ratios"],
        strike_step=config["strike_step"],
        strike_half_width=config["strike_half_width"],
        contracts_k2=config.get("contracts_per_leg", 1),
        product_prefix=config["product_prefix"],
        point_value=config.get("point_value", 1000.0),
    )

    timing_factor = None
    if config.get("enable_timing_factor", False):
        timing_factor = BullBearRegime()
        continuous_futures = build_continuous_futures_series(futures_data)
        timing_factor.fit({"futures": continuous_futures})

    engine = BacktestEngine(
        futures_data=futures_data,
        options_data=options_data,
        strategy=strategy,
        window=window,
        grid_search=grid_search,
        config=config,
        timing_factor=timing_factor,
    )

    # 5. 运行回测
    print("[INFO] 运行回测 ...")
    result = engine.run(start_date, end_date)
    print(f"[INFO] 共 {len(result.trades)} 笔交易")

    # 6. 计算指标
    metrics = calculate_metrics(
        equity_curve=result.equity_curve,
        trade_pnls=result.trade_pnls,
        risk_free_rate=config.get("risk_free_rate", 0.04),
    )

    # 7. 输出报告
    report_path = report_dir / f"put_ratio_backtest_{end_date.strftime('%Y%m%d')}.html"
    render_report(result, metrics, report_path)

    print("\n========== Backtest Result ==========")
    for k, v in metrics.to_dict().items():
        print(f"  {k}: {v}")
    print(f"\n[OK] HTML report saved: {report_path}")
    return report_path


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="Put Ratio Spread Backtest")
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="配置文件路径",
    )
    parser.add_argument("--start-date", type=str, help="覆盖回测起始日期")
    parser.add_argument("--end-date", type=str, help="覆盖回测终止日期")
    parser.add_argument("--window-type", type=str, choices=["rolling", "expanding"])
    parser.add_argument("--train-months", type=int, help="训练窗口长度（月）")

    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[ERROR] 配置文件不存在: {config_path}")
        return 1

    config = load_config(config_path)
    try:
        run_backtest(config, args)
    except Exception as exc:
        print(f"[ERROR] 回测失败: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
