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
import copy
import datetime as dt
import math
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd
import yaml

from src.core.engine import BacktestEngine
from src.core.strategy import MonthlyPutRatioSpreadStrategy
from src.core.windows import create_window
from src.data.fetcher import BloombergDataFetcher
from src.data.futures_calendar import active_contracts_in_range, front_month_ticker
from src.data.option_ticker import (
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


def resolve_ratio_grid(config: dict) -> List[float]:
    """Resolve a configured decimal ratio range to deterministic candidates."""
    ratio_range = config.get("ratio_range")
    if not ratio_range:
        ratios = [float(value) for value in config.get("ratios", [1.0])]
        if any(not math.isfinite(value) or value <= 0 or value > 2 for value in ratios):
            raise ValueError("ratios must contain finite values in the range (0, 2]")
        return ratios
    start = float(ratio_range.get("min", 0.1))
    stop = float(ratio_range.get("max", 2.0))
    step = float(ratio_range.get("step", 0.1))
    if (
        not all(math.isfinite(value) for value in (start, stop, step))
        or step <= 0
        or start <= 0
        or stop < start
        or stop > 2
    ):
        raise ValueError("ratio_range requires 0 < min <= max <= 2 and step > 0")
    count = round((stop - start) / step)
    if abs(start + count * step - stop) > 1e-9:
        raise ValueError("ratio_range max must lie on the configured step grid")
    return [round(start + i * step, 10) for i in range(count + 1)]


def build_all_option_tickers(
    futures_data: Dict[str, pd.DataFrame],
    config: dict,
) -> List[str]:
    """Build option tickers needed by each monthly entry date.

    Generate strikes from that date's available front-month futures price. This
    avoids missing intermediate price ranges and requesting unrelated strikes.
    """
    if not futures_data:
        raise RuntimeError("futures_data 为空，无法构建行权价网格。")

    # 校验每个合约都有 PX_LAST 列
    for ticker, df in futures_data.items():
        if "PX_LAST" not in df.columns:
            raise RuntimeError(
                f"期货合约 {ticker} 缺少 PX_LAST 列，可用列：{list(df.columns)}"
            )

    step = float(config["strike_step"])
    n = int(round(float(config["strike_half_width"]) / step))
    all_dates = pd.DatetimeIndex([], tz=None)
    for df in futures_data.values():
        all_dates = all_dates.union(pd.DatetimeIndex(df.index))
    all_dates = all_dates.sort_values()

    tickers: set[str] = set()
    handled_months: set[tuple[int, int]] = set()
    option_type = config["option_type"]
    prefix = config["product_prefix"]
    product_code = config.get("product_code", prefix)
    roll_on_first_day = config.get("roll_on_first_day_of_delivery_month", True)
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
        future_price = pd.to_numeric(
            pd.Series([future_df.loc[ts, "PX_LAST"]]), errors="coerce"
        ).iloc[0]
        if pd.isna(future_price):
            continue
        handled_months.add(month_key)

        series_candidates = list_option_series(
            start_date=decision_date,
            end_date=decision_date,
            future_ticker=future_ticker,
            expiry_overrides=config.get("expiry_overrides"),
        )
        series_candidates = [
            series for series in series_candidates
            if (series.expiry_date - decision_date).days >= 2
        ]
        if not series_candidates:
            continue

        center = round(float(future_price) / step) * step
        for series in series_candidates:
            for i in range(-n, n + 1):
                strike = round(center + i * step, 6)
                tickers.add(
                    build_option_ticker(prefix, series, option_type, strike)
                )
    return sorted(tickers)

def build_continuous_futures_series(
    futures_data: Dict[str, pd.DataFrame],
    product_code: str = "TY",
    roll_on_first_day_of_delivery_month: bool = True,
) -> pd.DataFrame:
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
        ticker = front_month_ticker(
            d, product_code,
            roll_on_first_day_of_delivery_month=roll_on_first_day_of_delivery_month,
        )
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
    effective_config = copy.deepcopy(config)
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
    if end_date < start_date:
        raise ValueError("end_date 必须不早于 start_date")
    effective_config.update({
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "window_type": window_type,
        "train_months": train_months,
    })
    effective_config["ratios"] = resolve_ratio_grid(config)
    effective_config["ratio_quantity_scale"] = int(
        config.get("ratio_quantity_scale", 10 if config.get("ratio_range") else 1)
    )
    if effective_config["ratio_quantity_scale"] < 1:
        raise ValueError("ratio_quantity_scale must be a positive integer")
    if any(
        abs(ratio * effective_config["ratio_quantity_scale"]
            - round(ratio * effective_config["ratio_quantity_scale"])) > 1e-9
        for ratio in effective_config["ratios"]
    ):
        raise ValueError(
            "ratio_quantity_scale cannot represent every ratio as integer contracts; "
            "increase the scale or adjust the ratio grid"
        )
    effective_config.setdefault("execution", {})
    effective_config["execution"].setdefault("max_bid_ask_spread_64", 12.0)
    data_config = effective_config.get("data", {})
    warmup_months = int(data_config.get("warmup_months", train_months))
    fetch_start = (pd.Timestamp(start_date) - pd.DateOffset(months=warmup_months)).date()
    effective_config["data_start_date"] = fetch_start.isoformat()
    effective_config["data_source"] = "Bloomberg/xbbg"
    effective_config.setdefault("settlement_field", "PX_LAST")
    effective_config["data_pull_started_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

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
        start_date=fetch_start,
        end_date=end_date,
        product_prefix=product_code,
        roll_on_first_day_of_delivery_month=config.get(
            "roll_on_first_day_of_delivery_month", True
        ),
    )
    print(f"[INFO] 共 {len(active_contracts)} 个近月合约: {', '.join(active_contracts)}")

    print("[INFO] 下载期货历史数据 ...")
    futures_data: Dict[str, pd.DataFrame] = {}
    futures_fields = list(config.get("futures_fields", ["PX_LAST"]))
    if effective_config["settlement_field"] not in futures_fields:
        futures_fields.append(effective_config["settlement_field"])
    for ticker in active_contracts:
        try:
            df = fetcher.fetch_futures_history(
                ticker=ticker,
                start_date=fetch_start,
                end_date=end_date,
                fields=futures_fields,
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
        start_date=fetch_start,
        end_date=end_date,
        fields=config.get("option_fields", ["PX_BID", "PX_ASK", "PX_LAST"]),
    )
    print(f"[INFO] 成功下载 {len(options_data)} 个期权 ticker 数据")
    effective_config["data_fetched_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    # 4. 初始化组件
    strategy = MonthlyPutRatioSpreadStrategy(
        strike_step=config["strike_step"],
        strike_half_width=config["strike_half_width"],
        contracts_k2=(config.get("contracts_per_leg", 1)
                      * effective_config["ratio_quantity_scale"]),
        product_prefix=config["product_prefix"],
        point_value=config.get("point_value", 1000.0),
        max_bid_ask_spread_64=effective_config["execution"]["max_bid_ask_spread_64"],
    )

    window = create_window(
        window_type=window_type,
        train_months=train_months,
        start_date=fetch_start,
    )

    grid_search = GridSearch(
        intervals=config["intervals"],
        ratios=effective_config["ratios"],
        strike_step=config["strike_step"],
        strike_half_width=config["strike_half_width"],
        contracts_k2=(config.get("contracts_per_leg", 1)
                      * effective_config["ratio_quantity_scale"]),
        product_prefix=config["product_prefix"],
        point_value=config.get("point_value", 1000.0),
        max_bid_ask_spread_64=effective_config["execution"]["max_bid_ask_spread_64"],
    )

    timing_factor = None
    if config.get("enable_timing_factor", False):
        timing_factor = BullBearRegime()
        continuous_futures = build_continuous_futures_series(
            futures_data,
            product_code=product_code,
            roll_on_first_day_of_delivery_month=config.get(
                "roll_on_first_day_of_delivery_month", True
            ),
        )
        timing_factor.fit({"futures": continuous_futures})

    engine = BacktestEngine(
        futures_data=futures_data,
        options_data=options_data,
        strategy=strategy,
        window=window,
        grid_search=grid_search,
        config=effective_config,
        timing_factor=timing_factor,
    )

    # 5. 运行回测
    print("[INFO] 运行回测 ...")
    result = engine.run(start_date, end_date)
    print(
        f"[INFO] 建仓 {len(result.trades)} 笔，已结算 {len(result.trade_pnls)} 笔，"
        f"持仓中 {sum(trade.status == 'open' for trade in result.trades)} 笔，"
        f"跳过 {len(result.skipped_trades)} 笔"
    )

    # 6. 计算指标
    metrics = calculate_metrics(
        equity_curve=result.equity_curve,
        trade_pnls=result.trade_pnls,
        risk_free_rate=effective_config.get("risk_free_rate", 0.04),
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
