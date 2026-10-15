"""
HTML 报告生成器。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from src.core.engine import BacktestResult
from src.metrics.performance import PerformanceMetrics


class HTMLReporter:
    """生成回测报告的 HTML 文件。"""

    def __init__(self, template_path: Path | str | None = None) -> None:
        """
        Args:
            template_path: HTML 模板路径；为 None 时使用默认模板。
        """
        if template_path is None:
            self.template_path = Path(__file__).resolve().parent / "report_template.html"
        else:
            self.template_path = Path(template_path)

    def _equity_curve_svg(self, equity: pd.Series, width: int = 800, height: int = 300) -> str:
        """生成净值曲线 SVG。"""
        if equity.empty:
            return ""

        values = equity.values
        dates = equity.index
        n = len(values)
        if n < 2:
            return ""

        min_v = values.min()
        max_v = values.max()
        pad = (max_v - min_v) * 0.1 or 1.0
        min_v -= pad
        max_v += pad

        pad_left = 60
        pad_right = 20
        pad_top = 20
        pad_bottom = 40
        w_inner = width - pad_left - pad_right
        h_inner = height - pad_top - pad_bottom

        def x(i: int) -> float:
            return pad_left + (i / (n - 1)) * w_inner

        def y(v: float) -> float:
            return pad_top + (max_v - v) / (max_v - min_v) * h_inner

        points = " ".join(f"{x(i):.1f},{y(values[i]):.1f}" for i in range(n))

        # 网格线
        grid_lines = ""
        steps = 5
        for i in range(steps + 1):
            v = min_v + (max_v - min_v) * i / steps
            grid_lines += (
                f'<line x1="{pad_left}" x2="{width - pad_right}" '
                f'y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#EDF2F7" stroke-width="1"/>'
            )
            grid_lines += (
                f'<text x="{pad_left - 8}" y="{y(v) + 4:.1f}" text-anchor="end" '
                f'font-size="10" fill="#A0AEC0">{v / 1e6:.2f}M</text>'
            )

        # 时间标签
        date_labels = ""
        label_indices = [0, n // 4, n // 2, 3 * n // 4, n - 1]
        for i in label_indices:
            d_str = dates[i].strftime("%Y-%m")
            date_labels += (
                f'<text x="{x(i):.1f}" y="{height - pad_bottom + 16}" '
                f'text-anchor="middle" font-size="10" fill="#718096">{d_str}</text>'
            )

        return f"""
        <svg viewBox="0 0 {width} {height}" style="width:100%;height:auto;background:#fff;border:1px solid #E2E8F0;border-radius:8px;">
            {grid_lines}
            <polyline points="{points}" fill="none" stroke="#2B6CB0" stroke-width="2"/>
            {date_labels}
            <text x="{width - pad_right}" y="{height - 8}" text-anchor="end" font-size="10" fill="#A0AEC0">日期 →</text>
        </svg>
        """

    def _params_table(self, result: BacktestResult) -> str:
        """生成每月参数表格 HTML。"""
        rows = ""
        for rd, params in result.monthly_params:
            rows += f"<tr><td>{rd}</td><td>{params.interval}</td><td>{params.ratio}</td></tr>"
        return f"""
        <table>
            <thead><tr><th>Date</th><th>Interval</th><th>Ratio</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
        """

    def _trades_table(self, result: BacktestResult) -> str:
        """生成交易明细表格 HTML。"""
        rows = ""
        for i, trade in enumerate(result.trades):
            pnl = result.trade_pnls[i] if i < len(result.trade_pnls) else 0.0
            cls = "pos" if pnl >= 0 else "neg"
            rows += (
                f"<tr>"
                f"<td>{trade.entry_date}</td>"
                f"<td>{trade.exit_date}</td>"
                f"<td>{trade.spread.k1}</td>"
                f"<td>{trade.spread.k2}</td>"
                f"<td>{trade.spread.ratio}</td>"
                f"<td class='{cls}'>{pnl:,.2f}</td>"
                f"</tr>"
            )
        return f"""
        <table>
            <thead>
                <tr><th>Entry</th><th>Exit</th><th>K1</th><th>K2</th><th>Ratio</th><th>Realized PnL</th></tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        """

    def _metrics_cards(self, metrics: PerformanceMetrics) -> str:
        """生成指标卡片 HTML。"""
        d = metrics.to_dict()
        cards = [
            ("Total Return", f"{d['total_return'] * 100:.2f}%"),
            ("Annual Return", f"{d['annual_return'] * 100:.2f}%"),
            ("Annual Volatility", f"{d['annual_volatility'] * 100:.2f}%"),
            ("Sharpe Ratio", f"{d['sharpe_ratio']:.2f}"),
            ("Sortino Ratio", f"{d['sortino_ratio']:.2f}"),
            ("Max Drawdown", f"{d['max_drawdown'] * 100:.2f}%"),
            ("Calmar Ratio", f"{d['calmar_ratio']:.2f}"),
            ("Win Rate", f"{d['win_rate'] * 100:.2f}%"),
            ("Avg Win", f"${d['avg_win']:,.2f}"),
            ("Avg Loss", f"${d['avg_loss']:,.2f}"),
            ("Profit Factor", f"{d['profit_factor']:.2f}"),
            ("Skewness", f"{d['skewness']:.2f}"),
            ("Kurtosis", f"{d['kurtosis']:.2f}"),
            ("VaR 95%", f"{d['var_95'] * 100:.2f}%"),
            ("CVaR 95%", f"{d['cvar_95'] * 100:.2f}%"),
            ("Num Trades", f"{d['num_trades']}"),
        ]
        html = '<div class="metrics-grid">'
        for label, value in cards:
            html += (
                f'<div class="metric-card">'
                f'<div class="metric-label">{label}</div>'
                f'<div class="metric-value">{value}</div>'
                f'</div>'
            )
        html += "</div>"
        return html

    def render(
        self,
        result: BacktestResult,
        metrics: PerformanceMetrics,
        output_path: Path | str,
    ) -> Path:
        """
        渲染并保存 HTML 报告。

        Args:
            result: 回测结果。
            metrics: 绩效指标。
            output_path: 输出文件路径。

        Returns:
            保存后的文件路径。
        """
        from jinja2 import Template

        template_text = self.template_path.read_text(encoding="utf-8")
        template = Template(template_text)

        equity_svg = self._equity_curve_svg(result.equity_curve)
        params_table = self._params_table(result)
        trades_table = self._trades_table(result)
        metrics_cards = self._metrics_cards(metrics)

        html = template.render(
            title="Put Ratio Spread Backtest Report",
            generated_at=dt.datetime.now().isoformat(timespec="seconds"),
            start_date=result.start_date,
            end_date=result.end_date,
            config=result.config,
            metrics=metrics.to_dict(),
            metrics_cards=metrics_cards,
            equity_svg=equity_svg,
            params_table=params_table,
            trades_table=trades_table,
            trade_count=len(result.trades),
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        return out


def render_report(
    result: BacktestResult,
    metrics: PerformanceMetrics,
    output_path: Path | str,
) -> Path:
    """便捷函数：生成 HTML 报告。"""
    reporter = HTMLReporter()
    return reporter.render(result, metrics, output_path)
