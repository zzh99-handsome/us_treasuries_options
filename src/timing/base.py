"""
择时因子抽象基类。

用于判断当前市场环境（如牛市/熊市），从而在不同 regime 下
选择不同的最优参数组合。
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from typing import Any, Dict


class TimingFactor(ABC):
    """择时因子抽象基类。"""

    @abstractmethod
    def fit(self, data: Dict[str, Any]) -> None:
        """
        在历史数据上拟合择时因子。

        Args:
            data: 因子所需数据，例如 {"futures": pd.DataFrame, ...}。
        """
        raise NotImplementedError

    @abstractmethod
    def regime(self, as_of_date: dt.date) -> str:
        """
        返回 as_of_date 当天的市场环境标签。

        Returns:
            例如 "bull", "bear", "neutral"。
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """因子名称。"""
        raise NotImplementedError
