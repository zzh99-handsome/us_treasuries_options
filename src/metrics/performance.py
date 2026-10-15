"""
回测绩效指标计算。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd


@dataclass
class PerformanceMetrics:
    """回测绩效指标集合。"""

    total_return: float
    annual_return: float
    annual_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_duration: int
    calmar_ratio: float
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    skewness: float
    kurtosis: float
    var_95: float
    cvar_95: float
    num_trades: int

    def to_dict(self) -> Dict[str, float]:
        """转换为字典。"""
        return {
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "annual_volatility": self.annual_volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_duration": self.max_drawdown_duration,
            "calmar_ratio": self.calmar_ratio,
            "win_rate": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "profit_factor": self.profit_factor,
            "skewness": self.skewness,
            "kurtosis": self.kurtosis,
            "var_95": self.var_95,
            "cvar_95": self.cvar_95,
            "num_trades": self.num_trades,
        }

    def to_dataframe(self) -> pd.DataFrame:
        """返回指标 DataFrame。"""
        return pd.DataFrame(
            [{"Metric": k, "Value": v} for k, v in self.to_dict().items()]
        )


def calculate_metrics(
    equity_curve: pd.Series,
    trade_pnls: Optional[list] = None,
    risk_free_rate: float = 0.04,
) -> PerformanceMetrics:
    """
    根据净值曲线计算绩效指标。

    Args:
        equity_curve: 以日期为索引的权益序列。
        trade_pnls: 每笔交易的实现盈亏列表（美元），用于统计胜率等。
        risk_free_rate: 年化无风险利率。

    Returns:
        PerformanceMetrics。
    """
    equity = equity_curve.dropna().sort_index()
    if len(equity) < 2:
        raise ValueError("净值曲线长度不足，无法计算指标")

    returns = equity.pct_change().dropna()
    if returns.empty:
        raise ValueError("收益率序列为空")

    # 按交易日年化（252 天）
    periods_per_year = 252
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    n_periods = len(returns)
    annual_return = (1.0 + total_return) ** (periods_per_year / n_periods) - 1.0
    annual_volatility = returns.std() * np.sqrt(periods_per_year)

    excess_returns = returns - risk_free_rate / periods_per_year
    sharpe_ratio = (
        excess_returns.mean() / returns.std() * np.sqrt(periods_per_year)
        if returns.std() > 0
        else 0.0
    )

    downside_returns = returns[returns < 0]
    downside_std = downside_returns.std() * np.sqrt(periods_per_year)
    sortino_ratio = (
        (returns.mean() * periods_per_year - risk_free_rate) / downside_std
        if downside_std > 0
        else 0.0
    )

    # 最大回撤与回撤持续时间
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    max_dd_duration = 0
    peak_idx = 0
    for i in range(len(equity)):
        if equity.iloc[i] >= equity.iloc[peak_idx]:
            peak_idx = i
        max_dd_duration = max(max_dd_duration, i - peak_idx)

    calmar_ratio = (
        annual_return / abs(max_drawdown) if max_drawdown < 0 else np.inf
    )

    # 交易统计
    if trade_pnls:
        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p < 0]
        win_rate = len(wins) / len(trade_pnls) if trade_pnls else 0.0
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0
        profit_factor = (
            sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else np.inf
        )
    else:
        win_rate = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        profit_factor = 0.0

    skewness = returns.skew()
    kurtosis = returns.kurtosis()
    var_95 = np.percentile(returns, 5)
    cvar_95 = returns[returns <= var_95].mean() if len(returns[returns <= var_95]) > 0 else var_95

    return PerformanceMetrics(
        total_return=total_return,
        annual_return=annual_return,
        annual_volatility=annual_volatility,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        max_drawdown=max_drawdown,
        max_drawdown_duration=max_dd_duration,
        calmar_ratio=calmar_ratio,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        skewness=skewness,
        kurtosis=kurtosis,
        var_95=var_95,
        cvar_95=cvar_95,
        num_trades=len(trade_pnls) if trade_pnls else 0,
    )
