"""
HTML 报告生成测试。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

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
    assert "Walk-Forward Training Diagnostics" in content
    assert "Methodology, Data and Limitations" in content
    assert "Ratio=0 is prohibited" in content
    assert 'class="table-scroll table-scroll--diagnostics"' in content
    assert 'class="table-scroll table-scroll--trades"' in content
    assert "{{" not in content


def test_diagnostics_table_keeps_summary_compact_and_expands_full_ranking() -> None:
    result = SimpleNamespace(window_diagnostics=[{
        "window_id": "W-20200401",
        "decision_date": "2020-04-01",
        "training_start": "2020-01-01",
        "training_end": "2020-03-31",
        "information_cutoff": "2020-03-31",
        "candidate_months": 3,
        "total_training_episodes": 3,
        "effective_samples": 2,
        "excluded_samples": 1,
        "exclusion_reasons": {"missing_quotes": 1},
        "objective": "cumulative_pnl",
        "regime": "neutral",
        "regime_sample_count": 2,
        "fallback": None,
        "selected_interval": 0.5,
        "selected_ratio": 1.2,
        "oos_trade_id": "202004-01",
        "best_score_usd": 125.0,
        "second_best_score_usd": 120.0,
        "score_gap_usd": 5.0,
        "relative_score_gap": 0.04,
        "parameter_instability_warning": False,
        "candidate_scores": [{
            "rank": 1, "interval": 0.5, "ratio": 1.2,
            "score_usd": 125.0, "valid_samples": 2,
        }],
    }])

    table = HTMLReporter()._diagnostics_table(result)

    assert "Samples (valid / total)" in table
    assert "<details>" in table
    assert "diagnostic-detail-panel" in table
    assert "Information cutoff" in table
    assert "Candidate months" in table
    assert "diagnostic-ranking" in table
    assert "missing_quotes" in table
    assert "<br>" not in table
