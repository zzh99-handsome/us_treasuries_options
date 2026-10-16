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
import sys
from pathlib import Path

import pandas as pd
import yaml

from src.core.backtest_builder import BacktestBuilder
from src.core.config_validator import resolve_ratio_grid
from src.metrics.performance import calculate_metrics
from src.report.reporter import render_report


def load_config(path: Path) -> dict:
    """加载 YAML 配置。"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_effective_config(
    config: dict,
    cli_args: argparse.Namespace | None,
) -> dict:
    """
    合并命令行覆盖并预处理配置。

    返回的 effective_config 可直接交给 BacktestBuilder。
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

    effective_config.setdefault("execution", {})
    effective_config["execution"].setdefault("max_bid_ask_spread_64", 12.0)

    data_config = effective_config.get("data", {})
    warmup_months = int(data_config.get("warmup_months", train_months))
    fetch_start = (pd.Timestamp(start_date) - pd.DateOffset(months=warmup_months)).date()
    effective_config["data_start_date"] = fetch_start.isoformat()

    effective_config["data_source"] = "Bloomberg/xbbg"
    effective_config.setdefault("settlement_field", "PX_LAST")
    effective_config["data_pull_started_at_utc"] = dt.datetime.now(
        dt.timezone.utc
    ).isoformat(timespec="seconds")

    return effective_config, start_date, end_date


def run_backtest(config: dict, cli_args: argparse.Namespace | None = None) -> Path:
    """
    执行完整回测流程。

    Args:
        config: 配置字典。
        cli_args: 命令行参数，可用于覆盖配置。

    Returns:
        生成的 HTML 报告路径。
    """
    effective_config, start_date, end_date = build_effective_config(config, cli_args)

    report_dir = Path(config.get("report_dir", "./reports"))
    report_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 回测区间: {start_date} ~ {end_date}")
    print(f"[INFO] 标的期货产品: {config['product_code']}（动态选择近月合约）")

    # 使用 BacktestBuilder 下载数据并组装引擎
    builder = BacktestBuilder(effective_config)
    engine = builder.build()

    effective_config["data_fetched_at_utc"] = dt.datetime.now(
        dt.timezone.utc
    ).isoformat(timespec="seconds")

    # 运行回测
    print("[INFO] 运行回测 ...")
    result = engine.run(start_date, end_date)
    print(
        f"[INFO] 建仓 {len(result.trades)} 笔，已结算 {len(result.trade_pnls)} 笔，"
        f"持仓中 {sum(trade.status == 'open' for trade in result.trades)} 笔，"
        f"跳过 {len(result.skipped_trades)} 笔"
    )

    # 根据配置选择收益分母
    performance_config = effective_config.get("performance", {})
    denom_type = performance_config.get("denominator", "initial_capital")
    if denom_type == "initial_capital":
        denom_value = float(effective_config.get("initial_capital", 1_000_000.0))
    elif denom_type == "notional":
        denom_value = sum(trade.notional_value for trade in result.trades)
    elif denom_type == "margin":
        denom_value = sum(trade.margin for trade in result.trades)
    else:
        raise ValueError(f"不支持的 performance.denominator: {denom_type}")
    if denom_value <= 0:
        print(f"[WARN] {denom_type} 分母值为 {denom_value}，回退到 initial_capital")
        denom_type = "initial_capital"
        denom_value = float(effective_config.get("initial_capital", 1_000_000.0))

    denominator_info = {"type": denom_type, "value": denom_value}

    # 计算指标
    metrics = calculate_metrics(
        equity_curve=result.equity_curve,
        trade_pnls=result.trade_pnls,
        denominator_info=denominator_info,
    )

    # 输出报告
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
