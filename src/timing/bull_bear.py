"""
牛熊市场判断择时因子（占位实现）。

默认返回 neutral，便于后续替换为基于均线、宏观或波动率的实现。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict

import pandas as pd

from src.timing.base import TimingFactor


class BullBearRegime(TimingFactor):
    """
    基于期货价格趋势判断牛熊市场。

    当前为占位实现，默认 neutral；可扩展为均线交叉、收益率等逻辑。
    """

    def __init__(
        self,
        short_window: int = 50,
        long_window: int = 200,
    ) -> None:
        """
        Args:
            short_window: 短期均线窗口（日）。
            long_window: 长期均线窗口（日）。
        """
        self.short_window = short_window
        self.long_window = long_window
        self._futures: pd.DataFrame | None = None

    @property
    def name(self) -> str:
        return f"BullBearRegime({self.short_window},{self.long_window})"

    def fit(self, data: Dict[str, Any]) -> None:
        """接收期货价格数据。"""
        futures = data.get("futures")
        if futures is not None:
            self._futures = futures.sort_index()

    def regime(self, as_of_date: dt.date) -> str:
        """
        判断 as_of_date 的市场环境。

        若未提供数据或窗口不足，返回 "neutral"。
        """
        if self._futures is None or self._futures.empty:
            return "neutral"

        ts = pd.Timestamp(as_of_date)
        if ts not in self._futures.index:
            return "neutral"

        idx = self._futures.index.get_loc(ts)
        if idx < self.long_window:
            return "neutral"

        window = self._futures.iloc[idx - self.long_window + 1 : idx + 1]["PX_LAST"]
        short_ma = window.iloc[-self.short_window:].mean()
        long_ma = window.mean()

        if short_ma > long_ma:
            return "bull"
        if short_ma < long_ma:
            return "bear"
        return "neutral"
