"""
配置校验器测试。
"""

from __future__ import annotations

import copy

import pytest

from src.core.config_validator import ConfigValidator, resolve_ratio_grid


@pytest.fixture
def base_config() -> dict:
    """合法基础配置。"""
    return {
        "product_code": "TY",
        "product_prefix": "TY",
        "start_date": "2020-01-01",
        "end_date": "2020-12-31",
        "option_type": "P",
        "strike_step": 0.25,
        "strike_half_width": 3.0,
        "intervals": [0.25, 0.5, 0.75, 1.0],
        "ratio_range": {"min": 0.5, "max": 2.5, "step": 0.1},
        "ratio_quantity_scale": 10,
        "window_type": "rolling",
        "train_months": 12,
        "initial_capital": 1_000_000.0,
        "point_value": 1000.0,
        "settlement_field": "SETTLE_PRICE",
        "contracts_per_leg": 1,
        "execution": {
            "buy_option_price": "ask",
            "sell_option_price": "bid",
            "invalid_quote_policy": "skip_trade",
            "max_bid_ask_spread_64": 12.0,
        },
        "backtest": {"hold_to_expiry": True, "roll_existing_positions": False},
        "strategy": {"require_same_expiry": True},
        "constraints": {"enabled": False},
        "optimization": {"objective": "cumulative_pnl"},
        "performance": {"denominator": "initial_capital", "margin_ratio": 0.1},
    }


def test_validate_valid_config(base_config: dict) -> None:
    """合法配置应通过校验。"""
    ConfigValidator.validate(base_config)


def test_validate_missing_required_field(base_config: dict) -> None:
    """缺少必填字段应抛 ValueError。"""
    config = copy.deepcopy(base_config)
    del config["initial_capital"]
    with pytest.raises(ValueError, match="initial_capital"):
        ConfigValidator.validate(config)


def test_validate_interval_not_multiple_of_step(base_config: dict) -> None:
    """interval 不是 strike_step 整数倍应抛 ValueError。"""
    config = copy.deepcopy(base_config)
    config["intervals"] = [0.3]
    with pytest.raises(ValueError, match="strike_step"):
        ConfigValidator.validate(config)


def test_validate_ratio_grid_non_integer_lot(base_config: dict) -> None:
    """ratio 与 scale 组合后卖出腿非整数应抛 ValueError。"""
    config = copy.deepcopy(base_config)
    config["ratio_quantity_scale"] = 3
    with pytest.raises(ValueError, match="卖出腿数量不是整数"):
        ConfigValidator.validate(config)


def test_validate_invalid_denominator(base_config: dict) -> None:
    """不支持的 denominator 应抛 ValueError。"""
    config = copy.deepcopy(base_config)
    config["performance"]["denominator"] = "equity"
    with pytest.raises(ValueError, match="denominator"):
        ConfigValidator.validate(config)


def test_validate_margin_requires_margin_ratio(base_config: dict) -> None:
    """denominator='margin' 时缺少 margin_ratio 应抛 ValueError。"""
    config = copy.deepcopy(base_config)
    config["performance"]["denominator"] = "margin"
    del config["performance"]["margin_ratio"]
    with pytest.raises(ValueError, match="margin_ratio"):
        ConfigValidator.validate(config)


def test_resolve_ratio_grid_0_5_to_2_5() -> None:
    """resolve_ratio_grid 应生成 0.5 到 2.5 步长 0.1 的 21 个 ratio。"""
    ratios = resolve_ratio_grid(
        {"ratio_range": {"min": 0.5, "max": 2.5, "step": 0.1}}
    )
    assert len(ratios) == 21
    assert ratios[0] == pytest.approx(0.5)
    assert ratios[-1] == pytest.approx(2.5)


def test_resolve_ratio_grid_rejects_zero_min() -> None:
    """min=0 的 ratio_range 应被拒绝。"""
    with pytest.raises(ValueError):
        resolve_ratio_grid({"ratio_range": {"min": 0, "max": 2, "step": 0.1}})
