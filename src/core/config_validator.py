"""
配置校验器。

集中校验回测配置合法性，避免在 engine、strategy、run_backtest 中散落校验逻辑。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional


class ConfigValidator:
    """回测配置校验器。"""

    @classmethod
    def validate(cls, config: Dict) -> None:
        """
        校验配置字典，不合法时抛出 ValueError。

        Args:
            config: 回测配置字典。
        """
        cls._validate_required_fields(config)
        cls._validate_numeric_fields(config)
        cls._validate_intervals(config)
        cls._validate_ratio_grid(config)
        cls._validate_execution(config)
        cls._validate_backtest(config)
        cls._validate_strategy(config)
        cls._validate_optimization(config)
        cls._validate_performance(config)

    @staticmethod
    def _validate_required_fields(config: Dict) -> None:
        """校验必填字段存在。"""
        required = [
            "product_code",
            "product_prefix",
            "start_date",
            "end_date",
            "option_type",
            "strike_step",
            "strike_half_width",
            "intervals",
            "ratio_range",
            "ratio_quantity_scale",
            "window_type",
            "train_months",
            "initial_capital",
            "point_value",
            "settlement_field",
            "contracts_per_leg",
        ]
        missing = [field for field in required if field not in config]
        if missing:
            raise ValueError(f"配置缺少必填字段: {missing}")

        if config.get("option_type") != "P":
            raise ValueError("当前策略只支持 option_type='P'")

        settlement_field = config.get("settlement_field")
        if not settlement_field or not isinstance(settlement_field, str):
            raise ValueError("settlement_field 必须是非空字符串")

    @staticmethod
    def _validate_numeric_fields(config: Dict) -> None:
        """校验数值型字段范围。"""
        positive_fields = [
            ("strike_step", "行权价步长"),
            ("strike_half_width", "行权价半宽"),
            ("ratio_quantity_scale", "ratio 数量缩放"),
            ("point_value", "每点美元"),
            ("initial_capital", "初始资金"),
            ("contracts_per_leg", "每腿合约数"),
        ]
        for field, label in positive_fields:
            value = config.get(field)
            if value is None or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{label} ({field}) 必须是正数")

        train_months = config.get("train_months")
        if not isinstance(train_months, int) or train_months <= 0:
            raise ValueError("train_months 必须是正整数")

        window_type = config.get("window_type")
        if window_type not in ("rolling", "expanding"):
            raise ValueError("window_type 必须是 'rolling' 或 'expanding'")

    @staticmethod
    def _validate_intervals(config: Dict) -> None:
        """校验 intervals 都在 strike_step 的整数倍上。"""
        strike_step = float(config["strike_step"])
        intervals = config.get("intervals", [])
        if not intervals:
            raise ValueError("intervals 不能为空")

        for interval in intervals:
            if not math.isfinite(interval) or interval <= 0:
                raise ValueError(f"intervals 必须为正数，收到 {interval}")
            if not math.isclose(interval / strike_step, round(interval / strike_step), abs_tol=1e-9):
                raise ValueError(
                    f"interval {interval} 不是 strike_step {strike_step} 的整数倍"
                )

    @staticmethod
    def _validate_ratio_grid(config: Dict) -> None:
        """校验 ratio_range 与 ratio_quantity_scale 能映射为整数张数。"""
        ratio_range = config.get("ratio_range")
        if not isinstance(ratio_range, dict):
            raise ValueError("ratio_range 必须是字典")

        min_ratio = float(ratio_range.get("min", 0.1))
        max_ratio = float(ratio_range.get("max", 2.0))
        step = float(ratio_range.get("step", 0.1))
        scale = int(config["ratio_quantity_scale"])

        if not all(math.isfinite(v) and v > 0 for v in (min_ratio, max_ratio, step)):
            raise ValueError("ratio_range 的 min/max/step 必须是正数")
        if max_ratio < min_ratio:
            raise ValueError("ratio_range.max 必须不小于 min")

        count = round((max_ratio - min_ratio) / step)
        if not math.isclose(min_ratio + count * step, max_ratio, abs_tol=1e-9):
            raise ValueError("ratio_range.max 必须落在 step 网格上")

        for i in range(count + 1):
            ratio = min_ratio + i * step
            sell_qty = ratio * scale
            if not math.isclose(sell_qty, round(sell_qty), abs_tol=1e-9):
                raise ValueError(
                    f"ratio={ratio} 与 ratio_quantity_scale={scale} 组合后 "
                    "卖出腿数量不是整数"
                )

    @staticmethod
    def _validate_execution(config: Dict) -> None:
        """校验执行模型配置。"""
        execution = config.get("execution", {})
        spread_limit = execution.get("max_bid_ask_spread_64")
        if spread_limit is not None and float(spread_limit) < 0:
            raise ValueError("execution.max_bid_ask_spread_64 必须非负")

        if execution.get("buy_option_price", "ask") != "ask":
            raise ValueError("当前执行模型仅支持买入腿使用 Ask")
        if execution.get("sell_option_price", "bid") != "bid":
            raise ValueError("当前执行模型仅支持卖出腿使用 Bid")
        if execution.get("invalid_quote_policy", "skip_trade") != "skip_trade":
            raise ValueError("当前仅支持 invalid_quote_policy=skip_trade")

    @staticmethod
    def _validate_backtest(config: Dict) -> None:
        """校验回测行为配置。"""
        backtest = config.get("backtest", {})
        if backtest.get("hold_to_expiry", True) is not True:
            raise ValueError("当前策略只支持持有至到期")
        if backtest.get("roll_existing_positions", False):
            raise ValueError("存续组合不能移仓；请设置 roll_existing_positions=false")

    @staticmethod
    def _validate_strategy(config: Dict) -> None:
        """校验策略配置。"""
        strategy = config.get("strategy", {})
        if strategy.get("require_same_expiry", True) is not True:
            raise ValueError("同一组合的所有期权腿必须使用相同到期日")

        if config.get("constraints", {}).get("enabled", False):
            raise ValueError("资本/保证金约束尚未实现，constraints.enabled 必须为 false")

    @staticmethod
    def _validate_optimization(config: Dict) -> None:
        """校验优化配置。"""
        optimization = config.get("optimization", {})
        objective = optimization.get("objective", "cumulative_pnl")
        if objective != "cumulative_pnl":
            raise ValueError(
                f"当前仅实现 optimization.objective=cumulative_pnl，收到 {objective!r}"
            )

    @staticmethod
    def _validate_performance(config: Dict) -> None:
        """校验绩效指标配置。"""
        performance = config.get("performance", {})
        denominator = performance.get("denominator", "initial_capital")
        valid_denominators = ("initial_capital", "notional", "margin")
        if denominator not in valid_denominators:
            raise ValueError(
                f"performance.denominator 必须是 {valid_denominators} 之一，"
                f"收到 {denominator!r}"
            )

        margin_ratio = performance.get("margin_ratio")
        if denominator == "margin":
            if margin_ratio is None or not math.isfinite(float(margin_ratio)) or float(margin_ratio) <= 0:
                raise ValueError("performance.denominator='margin' 时 margin_ratio 必须是正数")


def resolve_ratio_grid(config: Dict) -> List[float]:
    """
    将配置的 ratio_range 解析为具体候选列表。

    Args:
        config: 配置字典。

    Returns:
        ratio 候选列表。

    Raises:
        ValueError: ratio 值不合法。
    """
    ratio_range = config.get("ratio_range")
    if not ratio_range:
        ratios = [float(value) for value in config.get("ratios", [1.0])]
        if any(not math.isfinite(value) or value <= 0 for value in ratios):
            raise ValueError("ratios must contain finite values greater than 0")
        return ratios

    start = float(ratio_range["min"])
    stop = float(ratio_range["max"])
    step = float(ratio_range["step"])
    if (
        not all(math.isfinite(value) for value in (start, stop, step))
        or step <= 0
        or start <= 0
        or stop < start
    ):
        raise ValueError("ratio_range requires 0 < min <= max and step > 0")
    count = round((stop - start) / step)
    if not math.isclose(start + count * step, stop, abs_tol=1e-9):
        raise ValueError("ratio_range max must lie on the configured step grid")
    return [round(start + i * step, 10) for i in range(count + 1)]
