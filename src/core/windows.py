"""
训练窗口抽象与实现。

用于确定网格搜索时可使用的历史数据区间，
防止 lookahead bias。
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd


class TrainingWindow(ABC):
    """训练窗口抽象基类。"""

    @abstractmethod
    def get_train_start(self, rebalance_date: dt.date) -> dt.date:
        """返回训练窗口起始日期（含）。"""
        raise NotImplementedError

    @abstractmethod
    def get_train_end(self, rebalance_date: dt.date) -> dt.date:
        """返回训练窗口结束日期（含），必须 <= rebalance_date。"""
        raise NotImplementedError

    def filter_data(
        self,
        data: pd.Series | pd.DataFrame,
        rebalance_date: dt.date,
    ) -> pd.Series | pd.DataFrame:
        """
        根据训练窗口截取数据。

        Args:
            data: 以日期为索引的 Series 或 DataFrame。
            rebalance_date: 重训练日期。

        Returns:
            截取后的数据视图。
        """
        start = pd.Timestamp(self.get_train_start(rebalance_date))
        end = pd.Timestamp(self.get_train_end(rebalance_date))
        return data.loc[start:end]


class RollingWindow(TrainingWindow):
    """固定长度的滚动训练窗口。"""

    def __init__(self, train_months: int) -> None:
        """
        Args:
            train_months: 训练窗口长度（月）。
        """
        if train_months <= 0:
            raise ValueError("train_months 必须大于 0")
        self.train_months = train_months

    def get_train_start(self, rebalance_date: dt.date) -> dt.date:
        """返回 rebalance_date 前 train_months 个月的月初。"""
        year = rebalance_date.year
        month = rebalance_date.month - self.train_months
        while month <= 0:
            year -= 1
            month += 12
        return dt.date(year, month, 1)

    def get_train_end(self, rebalance_date: dt.date) -> dt.date:
        """训练数据截止到 rebalance_date 前一天，避免 lookahead。"""
        return rebalance_date - dt.timedelta(days=1)

    def __repr__(self) -> str:
        return f"RollingWindow({self.train_months} months)"


class ExpandingWindow(TrainingWindow):
    """从固定起点逐渐扩展的训练窗口。"""

    def __init__(self, start_date: dt.date, min_months: int = 0) -> None:
        """
        Args:
            start_date: 训练窗口固定起点。
            min_months: 最小训练长度（月），不足时返回空窗口。
        """
        self.start_date = start_date
        self.min_months = min_months

    def get_train_start(self, rebalance_date: dt.date) -> dt.date:
        """返回固定起点。"""
        return self.start_date

    def get_train_end(self, rebalance_date: dt.date) -> dt.date:
        """训练数据截止到 rebalance_date 前一天。"""
        return rebalance_date - dt.timedelta(days=1)

    def is_valid(self, rebalance_date: dt.date) -> bool:
        """检查窗口长度是否达到最小要求。"""
        if rebalance_date <= self.start_date:
            return False
        delta_months = (
            (rebalance_date.year - self.start_date.year) * 12
            + rebalance_date.month
            - self.start_date.month
        )
        return delta_months >= self.min_months

    def __repr__(self) -> str:
        return f"ExpandingWindow(from {self.start_date}, min {self.min_months} months)"


def create_window(
    window_type: str,
    train_months: int,
    start_date: Optional[dt.date] = None,
) -> TrainingWindow:
    """
    工厂函数，根据配置创建训练窗口。

    Args:
        window_type: "rolling" 或 "expanding"。
        train_months: 窗口长度（月）。
        start_date: expanding 窗口所需起点。

    Returns:
        TrainingWindow 实例。
    """
    if window_type == "rolling":
        return RollingWindow(train_months)
    if window_type == "expanding":
        if start_date is None:
            raise ValueError("expanding 窗口需要提供 start_date")
        return ExpandingWindow(start_date, min_months=train_months)
    raise ValueError(f"不支持的窗口类型: {window_type}")
