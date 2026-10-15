"""
网格搜索模块。

用于在训练窗口内搜索最优的 Put Ratio Spread 参数组合
（interval 与 ratio），默认优化目标为训练期内每月累计 payoff。
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from src.core.instrument import PutRatioSpread


@dataclass(frozen=True)
class ParameterSet:
    """一组策略参数。"""

    interval: float
    ratio: float


@dataclass
class Episode:
    """
    一个历史交易 episode，用于网格搜索中的模拟。

    Attributes:
        entry_date: 建仓日期（每月初）。
        expiry_date: 到期日期。
        future_price_entry: 建仓日期期货价格。
        future_price_expiry: 到期日期期货价格。
        option_chain: 建仓日期期权链 {strike: {"bid": float, "ask": float}}。
    """

    entry_date: dt.date
    expiry_date: dt.date
    future_price_entry: float
    future_price_expiry: float
    option_chain: Dict[float, Dict[str, Optional[float]]]
    future_ticker: str = ""
    regime: str = "neutral"


@dataclass
class GridSearchResult:
    """网格搜索结果。"""

    best_params: ParameterSet
    best_score: float
    scores: Dict[ParameterSet, float]
    episodes_count: int
    valid_counts: Dict[ParameterSet, int] = field(default_factory=dict)

    @property
    def ranked_scores(self) -> List[Tuple[ParameterSet, float]]:
        """所有候选按分数降序排列；同分时按 interval、ratio 升序确定性排序。"""
        return sorted(
            self.scores.items(),
            key=lambda item: (-item[1], item[0].interval, item[0].ratio),
        )


ObjectiveFunc = Callable[[List[float]], float]


def default_objective(monthly_pnls: List[float]) -> float:
    """
    默认优化目标：训练窗口内每月 payoff 的总和。

    Args:
        monthly_pnls: 每月实现盈亏列表。

    Returns:
        累计盈亏。
    """
    return sum(monthly_pnls)


class GridSearch:
    """
    Put Ratio Spread 参数网格搜索器。
    """

    def __init__(
        self,
        intervals: Sequence[float],
        ratios: Sequence[float],
        strike_step: float,
        strike_half_width: float,
        contracts_k2: int,
        product_prefix: str,
        point_value: float = 1000.0,
        objective: ObjectiveFunc = default_objective,
        max_bid_ask_spread_64: Optional[float] = None,
    ) -> None:
        """
        Args:
            intervals: 待搜索的 K2-K1 间距列表。
            ratios: 待搜索的 ratio 列表。
            strike_step: 行权价网格步长。
            strike_half_width: 行权价网格半宽。
            contracts_k2: 买入 K2 Put 的合约张数。
            product_prefix: 期权代码前缀。
            point_value: 每点美元。
            objective: 优化目标函数，默认累计 payoff。
        """
        self.intervals = list(intervals)
        self.ratios = [float(ratio) for ratio in ratios]
        if not self.ratios or any(
            not math.isfinite(ratio) or ratio <= 0 or ratio > 2
            for ratio in self.ratios
        ):
            raise ValueError("ratios must contain finite values in the range (0, 2]")
        self.strike_step = strike_step
        self.strike_half_width = strike_half_width
        self.contracts_k2 = contracts_k2
        self.product_prefix = product_prefix
        self.point_value = point_value
        self.objective = objective
        self.max_bid_ask_spread_64 = max_bid_ask_spread_64

    def _simulate_episode(
        self,
        params: ParameterSet,
        episode: Episode,
    ) -> Optional[float]:
        """
        用给定参数模拟单个 episode 的实现盈亏。

        Returns:
            实现盈亏（美元）；若无法构建 spread 则返回 None。
        """
        spread = PutRatioSpread.build(
            future_price=episode.future_price_entry,
            strike_step=self.strike_step,
            strike_half_width=self.strike_half_width,
            interval=params.interval,
            ratio=params.ratio,
            option_chain=episode.option_chain,
            contracts_k2=self.contracts_k2,
            entry_date=episode.entry_date,
            expiry_date=episode.expiry_date,
            product_prefix=self.product_prefix,
            point_value=self.point_value,
            max_bid_ask_spread_64=self.max_bid_ask_spread_64,
        )
        if spread is None:
            return None
        return spread.realized_pnl(episode.future_price_expiry)

    def search(self, episodes: Sequence[Episode]) -> GridSearchResult:
        """
        在历史 episode 上执行网格搜索。

        Args:
            episodes: 训练窗口内的历史 episode 列表。

        Returns:
            GridSearchResult，包含最优参数与所有参数得分。
        """
        if not episodes:
            raise ValueError("episodes 不能为空")

        scores: Dict[ParameterSet, List[Optional[float]]] = {}
        for interval in self.intervals:
            for ratio in self.ratios:
                params = ParameterSet(interval=interval, ratio=ratio)
                scores[params] = [
                    self._simulate_episode(params, ep) for ep in episodes
                ]

        # 对每组参数剔除不可执行样本，并记录有效样本数。
        avg_scores: Dict[ParameterSet, float] = {}
        valid_counts: Dict[ParameterSet, int] = {}
        for params, pnls in scores.items():
            valid_pnls = [p for p in pnls if p is not None]
            valid_counts[params] = len(valid_pnls)
            if not valid_pnls:
                avg_scores[params] = -float("inf")
            else:
                avg_scores[params] = self.objective(valid_pnls)

        ranked = sorted(
            avg_scores.items(),
            key=lambda item: (-item[1], item[0].interval, item[0].ratio),
        )
        best_params = ranked[0][0]
        return GridSearchResult(
            best_params=best_params,
            best_score=avg_scores[best_params],
            scores=avg_scores,
            episodes_count=len(episodes),
            valid_counts=valid_counts,
        )

    def top_k(
        self,
        episodes: Sequence[Episode],
        k: int = 5,
    ) -> List[Tuple[ParameterSet, float]]:
        """
        返回得分最高的 k 组参数。

        Args:
            episodes: 训练 episode 列表。
            k: 返回前几名。

        Returns:
            [(ParameterSet, score), ...]
        """
        result = self.search(episodes)
        return result.ranked_scores[:k]
