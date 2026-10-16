"""
绩效指标测试。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.metrics.performance import calculate_metrics


def test_metrics_known_sequence() -> None:
    """
    构造一个已知净值序列验证指标。

    净值每日固定上涨 0.1%（252 个交易日，251 个收益率），则：
    - 总收益 ≈ 1.001^251 - 1
    - 年化收益 ≈ 28.4%
    - 年化波动 ≈ 0
    """
    dates = pd.date_range("2020-01-01", periods=252, freq="B")
    equity = 1_000_000 * (1.001 ** np.arange(len(dates)))
    equity_series = pd.Series(equity, index=dates)

    metrics = calculate_metrics(equity_series)
    expected_total = 1.001 ** 251 - 1
    expected_annual = 1.001 ** 252 - 1
    assert metrics.total_return == pytest.approx(expected_total, rel=1e-4)
    assert metrics.annual_return == pytest.approx(expected_annual, rel=1e-4)
    assert metrics.max_drawdown == pytest.approx(0.0, abs=1e-6)


def test_metrics_drawdown() -> None:
    """验证最大回撤计算。"""
    dates = pd.date_range("2020-01-01", periods=10, freq="B")
    equity = pd.Series(
        [100.0, 110.0, 105.0, 95.0, 100.0, 90.0, 85.0, 95.0, 100.0, 110.0],
        index=dates,
    )
    metrics = calculate_metrics(equity)
    # 峰值 110，最低 85，回撤 (85-110)/110 ≈ -22.73%
    assert metrics.max_drawdown == pytest.approx((85.0 - 110.0) / 110.0, rel=1e-4)


def test_metrics_with_trade_pnls() -> None:
    """验证交易统计指标。"""
    dates = pd.date_range("2020-01-01", periods=10, freq="B")
    equity = pd.Series(np.linspace(100, 110, 10), index=dates)
    trade_pnls = [1000.0, -500.0, 2000.0, -1000.0]
    metrics = calculate_metrics(equity, trade_pnls=trade_pnls)
    assert metrics.num_trades == 4
    assert metrics.win_rate == pytest.approx(0.5)
    assert metrics.avg_win == pytest.approx(1500.0)
    assert metrics.avg_loss == pytest.approx(-750.0)


def test_metrics_insufficient_data() -> None:
    """数据不足时应抛出 ValueError。"""
    equity = pd.Series([100.0], index=pd.date_range("2020-01-01", periods=1))
    with pytest.raises(ValueError):
        calculate_metrics(equity)


def test_metrics_notional_denominator() -> None:
    """使用 notional 分母时收益应以名义仓位为基准，且不扣除无风险利率。"""
    dates = pd.date_range("2020-01-01", periods=10, freq="B")
    equity = pd.Series(np.linspace(1_000_000, 1_100_000, 10), index=dates)
    metrics = calculate_metrics(
        equity,
        denominator_info={"type": "notional", "value": 2_000_000},
    )
    assert metrics.total_return == pytest.approx(100_000 / 2_000_000)
    assert metrics.sharpe_ratio == pytest.approx(
        (equity.diff().dropna() / 2_000_000).mean()
        / (equity.diff().dropna() / 2_000_000).std()
        * np.sqrt(252),
        rel=1e-4,
    )


def test_metrics_margin_denominator() -> None:
    """使用 margin 分母时收益应以保证金占用为基准。"""
    dates = pd.date_range("2020-01-01", periods=10, freq="B")
    equity = pd.Series(np.linspace(1_000_000, 1_050_000, 10), index=dates)
    metrics = calculate_metrics(
        equity,
        denominator_info={"type": "margin", "value": 500_000},
    )
    assert metrics.total_return == pytest.approx(50_000 / 500_000)


def test_metrics_sharpe_uses_zero_risk_free_rate() -> None:
    """夏普比率分子不使用超额收益，即不扣除无风险利率。"""
    dates = pd.date_range("2020-01-01", periods=252, freq="B")
    equity = 1_000_000 * (1.001 ** np.arange(len(dates)))
    equity_series = pd.Series(equity, index=dates)
    metrics = calculate_metrics(equity_series)
    returns = equity_series.diff().dropna() / equity_series.iloc[0]
    expected_sharpe = returns.mean() / returns.std() * np.sqrt(252)
    assert metrics.sharpe_ratio == pytest.approx(expected_sharpe, rel=1e-4)
