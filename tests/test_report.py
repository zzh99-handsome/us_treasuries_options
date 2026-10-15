"""
HTML 报告生成测试。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from src.metrics.performance import calculate_metrics
from src.optimization.grid_search import ParameterSet
from src.report.reporter import HTMLReporter


def test_reporter_generates_html(tmp_path: Path, sample_config: dict) -> None:
    """报告生成器应输出有效 HTML 文件。"""
    import pandas as pd

    from src.core.engine import BacktestResult

    dates = pd.date_range("2020-04-01", "2020-12-31", freq="B")
    equity = pd.Series([1_000_000.0 + i * 100 for i in range(len(dates))], index=dates)
    result = BacktestResult(
        config=sample_config,
        equity_curve=equity,
        trades=[],
        trade_pnls=[],
        monthly_params=[(dt.date(2020, 4, 1), ParameterSet(interval=0.5, ratio=1.5))],
        grid_search_history=[],
    )
    metrics = calculate_metrics(equity, risk_free_rate=0.04)

    reporter = HTMLReporter()
    out = tmp_path / "report.html"
    reporter.render(result, metrics, out)

    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "Put Ratio Spread Backtest Report" in content
    assert "Equity Curve" in content
    assert "Performance Metrics" in content
