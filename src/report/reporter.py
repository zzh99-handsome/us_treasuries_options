"""
HTML 报告生成器。
"""

from __future__ import annotations

import datetime as dt
import html
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
        for trade in result.trades:
            pnl = trade.realized_pnl
            cls = "pos" if pnl is not None and pnl >= 0 else "neg"
            pnl_text = f"${pnl:,.2f}" if pnl is not None else "未结算"
            bid_time = trade.spread.bid_k1_quote_date.isoformat() if trade.spread.bid_k1_quote_date else "—"
            ask_time = trade.spread.ask_k2_quote_date.isoformat() if trade.spread.ask_k2_quote_date else "—"
            settle_cf = (
                f"${trade.settlement_cashflow:,.2f}"
                if trade.settlement_cashflow is not None else "—"
            )
            rows += (
                f"<tr>"
                f"<td>{html.escape(trade.trade_id)}</td>"
                f"<td>{trade.entry_date}</td>"
                f"<td>{trade.exit_date}</td>"
                f"<td>{html.escape(trade.future_ticker)}</td>"
                f"<td>{html.escape(trade.spread.low_put.ticker)}</td>"
                f"<td>{html.escape(trade.spread.high_put.ticker)}</td>"
                f"<td>{trade.spread.k1}</td>"
                f"<td>{trade.spread.k2}</td>"
                f"<td>{trade.spread.buy_qty}</td>"
                f"<td>{trade.spread.sell_qty}</td>"
                f"<td>{trade.spread.bid_k1_64:g}</td>"
                f"<td>{html.escape(bid_time)}</td>"
                f"<td>{trade.spread.ask_k2_64:g}</td>"
                f"<td>{html.escape(ask_time)}</td>"
                f"<td>${trade.spread.sell_premium_cashflow:,.2f}</td>"
                f"<td>${trade.spread.buy_premium_cashflow:,.2f}</td>"
                f"<td>${trade.spread.premium_cashflow:,.2f}</td>"
                f"<td>{trade.settlement_price if trade.settlement_price is not None else '—'}</td>"
                f"<td>{settle_cf}</td>"
                f"<td>{html.escape(trade.status)}</td>"
                f"<td class='{cls}'>{pnl_text}</td>"
                f"</tr>"
            )
        return f"""
        <table>
            <thead>
                <tr><th>Trade ID</th><th>Entry</th><th>Expiry</th><th>Future</th><th>Sell Put</th><th>Buy Put</th><th>K1</th><th>K2</th><th>Buy Qty</th><th>Sell Qty</th><th>Sell Bid (assumed 1/64)</th><th>Bid date index</th><th>Buy Ask (assumed 1/64)</th><th>Ask date index</th><th>Sell cashflow</th><th>Buy cashflow</th><th>Entry net cashflow</th><th>Expiry price ({result.config.get('settlement_field', 'PX_LAST')})</th><th>Expiry cashflow</th><th>Status</th><th>Total P&amp;L</th></tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        """

    def _diagnostics_table(self, result: BacktestResult) -> str:
        """Render compact window summaries with expandable full diagnostics."""
        rows = ""
        for item in result.window_diagnostics:
            def money(value: Any) -> str:
                return "—" if value is None else f"${float(value):,.2f}"

            def detail(label: str, value: Any) -> str:
                return (
                    f"<div><dt>{html.escape(label)}</dt>"
                    f"<dd>{html.escape(str(value if value is not None else '—'))}</dd></div>"
                )

            candidates = item.get("candidate_scores", [])
            candidate_rows = "".join(
                "<tr>"
                f"<td>{html.escape(str(candidate.get('rank', '—')))}</td>"
                f"<td>{html.escape(str(candidate.get('interval', '—')))}</td>"
                f"<td>{html.escape(str(candidate.get('ratio', '—')))}</td>"
                f"<td>{money(candidate.get('score_usd'))}</td>"
                f"<td>{html.escape(str(candidate.get('valid_samples', 0)))}</td>"
                "</tr>"
                for candidate in candidates
            ) or '<tr><td colspan="5">No scored candidates</td></tr>'
            reasons = json.dumps(
                item.get("exclusion_reasons", {}), ensure_ascii=False, sort_keys=True
            )
            score_gap = item.get("score_gap_usd")
            relative_gap = item.get("relative_score_gap")
            selected = (
                f"I {item.get('selected_interval', '—')} / "
                f"R {item.get('selected_ratio', '—')}"
            )
            regime = str(item.get("regime", "neutral"))
            regime_samples = item.get("regime_sample_count", 0)
            date_window = (
                f"{item.get('training_start', '—')} – {item.get('training_end', '—')}"
            )
            decision_date = item.get("decision_date", "—")
            detail_panel = (
                '<div class="diagnostic-detail-panel"><div class="diagnostic-meta">'
                + detail("Decision date", decision_date)
                + detail("Information cutoff", item.get("information_cutoff"))
                + detail("Training range", date_window)
                + detail("Objective", item.get("objective"))
                + detail("Candidate months", item.get("candidate_months"))
                + detail("Training episodes", item.get("total_training_episodes"))
                + detail("Effective samples", item.get("effective_samples"))
                + detail("Excluded samples", item.get("excluded_samples"))
                + detail("Regime samples", regime_samples)
                + detail("Fallback", item.get("fallback") or "None")
                + detail("Selected parameters", selected)
                + detail("Best score", money(item.get("best_score_usd")))
                + detail("Second-best score", money(item.get("second_best_score_usd")))
                + detail("Best / second gap", money(score_gap))
                + detail(
                    "Relative gap",
                    "—" if relative_gap is None else f"{float(relative_gap):.2%}",
                )
                + detail(
                    "Stability",
                    "Near tie; parameters may be unstable"
                    if item.get("parameter_instability_warning") else "No near-tie warning",
                )
                + '</div><div class="diagnostic-reasons"><strong>Exclusion reasons</strong>'
                f"<pre>{html.escape(reasons)}</pre></div>"
                '<div class="diagnostic-ranking-scroll"><table class="diagnostic-ranking">'
                "<thead><tr><th>Rank</th><th>Interval</th><th>Ratio</th>"
                "<th>Score (USD)</th><th>Valid n</th></tr></thead>"
                f"<tbody>{candidate_rows}</tbody></table></div></div>"
            )
            rows += (
                "<tr>"
                f"<td>{html.escape(item['window_id'])}</td>"
                f"<td>{html.escape(str(decision_date))}<small>{html.escape(date_window)}</small></td>"
                f"<td>{item.get('effective_samples', 0)} / {item.get('total_training_episodes', 0)}"
                f"<small>{item.get('excluded_samples', 0)} excluded</small></td>"
                f"<td>{html.escape(regime)}<small>n={html.escape(str(regime_samples))}</small></td>"
                f"<td>{html.escape(selected)}</td>"
                f"<td>{money(item.get('best_score_usd'))}"
                f"<small>gap {money(score_gap)} / "
                f"{'—' if relative_gap is None else f'{float(relative_gap):.2%}'}</small></td>"
                f"<td>{html.escape(str(item.get('oos_trade_id', '—')))}</td>"
                f'<td class="diagnostic-detail-cell"><details>'
                f"<summary>View</summary>{detail_panel}</details></td>"
                "</tr>"
            )
        return (
            '<table class="diagnostics-summary"><thead><tr>'
            "<th>Window</th><th>Decision / Training</th><th>Samples (valid / total)</th>"
            "<th>Regime</th><th>Selected (I / R)</th><th>Best score / gap</th>"
            "<th>OOS trade</th><th>Details</th></tr></thead><tbody>"
            + rows
            + "</tbody></table>"
        )

    def _skipped_table(self, result: BacktestResult) -> str:
        """生成跳过交易及原因表。"""
        rows = "".join(
            f"<tr><td>{html.escape(str(item.get('trade_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('entry_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('expiry_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('reason', '')))}</td></tr>"
            for item in result.skipped_trades
        )
        return "<table><thead><tr><th>Trade ID</th><th>Entry</th><th>Expiry</th><th>Reason</th></tr></thead><tbody>" + rows + "</tbody></table>"

    def _breakdown_table(self, result: BacktestResult) -> str:
        """按入场月份、参数和 regime 汇总已结算交易 P&L。"""
        grouped: Dict[tuple, Dict[str, float]] = {}
        for trade in result.trades:
            key = (
                trade.entry_date.strftime("%Y-%m"),
                trade.params_interval,
                trade.params_ratio,
                trade.regime,
            )
            stats = grouped.setdefault(key, {"settled": 0, "open": 0, "pnl": 0.0})
            if trade.status == "settled" and trade.realized_pnl is not None:
                stats["settled"] += 1
                stats["pnl"] += trade.realized_pnl
            else:
                stats["open"] += 1
        rows = "".join(
            f"<tr><td>{month}</td><td>{interval}</td><td>{ratio}</td>"
            f"<td>{html.escape(regime)}</td><td>{stats['settled']}</td>"
            f"<td>{stats['open']}</td><td>${stats['pnl']:,.2f}</td></tr>"
            for (month, interval, ratio, regime), stats in sorted(grouped.items())
        )
        return "<table><thead><tr><th>Entry month</th><th>Interval</th><th>Sell / buy ratio</th><th>Regime</th><th>Settled</th><th>Open</th><th>Realized P&amp;L (USD)</th></tr></thead><tbody>" + rows + "</tbody></table>"

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
            skipped_table=self._skipped_table(result),
            breakdown_table=self._breakdown_table(result),
            diagnostics_table=self._diagnostics_table(result),
            skipped_count=len(result.skipped_trades),
            settled_count=sum(trade.status == "settled" for trade in result.trades),
            open_count=sum(trade.status == "open" for trade in result.trades),
            config_json=json.dumps(result.config, ensure_ascii=False, indent=2, default=str),
            methodology_notes=[
                "Equity curve is initial capital plus cumulative daily mark-to-market P&L. Open positions are marked each trading day using an intrinsic-value approximation (no time value or volatility); expiry P&L is realized once using the settlement price.",
                f"Return denominator: {result.config.get('performance', {}).get('denominator', 'initial_capital')}. Supported types: initial_capital (configured initial capital), notional (sum of buy-leg notional across trades), margin (notional * margin_ratio).",
                "cumulative_pnl objective = sum of valid matured training-trade total P&L in USD; invalid candidates are excluded, higher scores are better, and ties use ascending interval then ratio.",
                "Entry uses sell-leg Bid and buy-leg Ask. Required legs need positive, uncrossed two-sided quotes; no Mid or Last fallback is used.",
                (
                    f"Liquidity screen: each used option market must have a displayed bid/ask width no greater than "
                    f"{result.config.get('execution', {}).get('max_bid_ask_spread_64', 'unlimited')} / 64 price points. "
                    "The engine tries eligible expiries nearest-first and skips only when none has a valid strategy pair; "
                    "daily quotes do not include displayed size, so this is a quote-quality filter, not a depth guarantee."
                ),
                (
                    f"Ratio means sell contracts / buy contracts. ratio_quantity_scale="
                    f"{result.config.get('ratio_quantity_scale', 1)}; buy quantity is contracts_per_leg times this scale, "
                    "and sell quantity is ratio times buy quantity. Ratio=0 is prohibited, so each strategy candidate "
                    "contains both bought and sold puts. Actual quantities are shown per trade in the ledger."
                ),
                f"Option chain source: {result.config.get('data', {}).get('option_chain_source', 'inferred')}; inferred builds strikes around the current futures price, while bloomberg_chain requests the exchange chain from Bloomberg when available.",
                f"Quote source: {result.config.get('data_source', 'unspecified')}; observations use daily date indexes, so intraday availability and quote age are not verified.",
                f"Expiry P&L uses configured settlement field {result.config.get('settlement_field', 'PX_LAST')} with fallback to PX_LAST if the field is missing; verify it matches the exchange final settlement source before interpreting real results.",
                f"Expiry dates follow the CME rule: the Friday before the last business day of the prior month, with U.S. federal holidays approximating CME closures. Manual overrides: {result.config.get('expiry_overrides')}.",
                f"Market specification status: {result.config.get('market_specifications', {}).get('verification_status', 'unverified')}; record exchange sources before interpreting real-market performance.",
                "Commissions, additional slippage, and bid/ask crossing slippage beyond the assumed entry prices are not modeled. Synthetic results are for software behavior only.",
            ],
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
