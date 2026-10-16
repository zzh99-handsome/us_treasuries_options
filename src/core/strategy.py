"""
月度 Put Ratio Spread 策略。

每月初根据给定参数构建一个 Put Ratio Spread，持有至期权到期。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.core.instrument import PutRatioSpread


@dataclass
class Trade:
    """一笔交易记录。"""

    entry_date: dt.date
    exit_date: dt.date
    spread: PutRatioSpread
    params_interval: float
    params_ratio: float
    trade_id: str = ""
    future_ticker: str = ""
    status: str = "open"
    realized_pnl: Optional[float] = None
    settlement_price: Optional[float] = None
    settlement_cashflow: Optional[float] = None
    regime: str = "neutral"
    window_id: str = ""
    notional_value: float = 0.0
    margin: float = 0.0


class MonthlyPutRatioSpreadStrategy:
    """
    月度 Put Ratio Spread 策略。

    在每个月初根据当前市场条件与指定参数构建 spread，
    并持有至该系列期权到期日。
    """

    def __init__(
        self,
        strike_step: float,
        strike_half_width: float,
        contracts_k2: int,
        product_prefix: str,
        point_value: float = 1000.0,
        max_bid_ask_spread_64: Optional[float] = None,
    ) -> None:
        """
        Args:
            strike_step: 行权价网格步长。
            strike_half_width: 行权价网格半宽。
            contracts_k2: 买入 K2 Put 的合约张数。
            product_prefix: 期权代码前缀。
            point_value: 每点美元。
        """
        self.strike_step = strike_step
        self.strike_half_width = strike_half_width
        self.contracts_k2 = contracts_k2
        self.product_prefix = product_prefix
        self.point_value = point_value
        self.max_bid_ask_spread_64 = max_bid_ask_spread_64

    def build_trade(
        self,
        rebalance_date: dt.date,
        expiry_date: dt.date,
        future_price: float,
        option_chain: Dict[float, Dict[str, Optional[float]]],
        interval: float,
        ratio: float,
    ) -> Optional[Trade]:
        """
        根据参数在指定日期建仓。

        Args:
            rebalance_date: 建仓日期。
            expiry_date: 期权到期日。
            future_price: 建仓时期货价格。
            option_chain: 建仓日期期权链。
            interval: K2 - K1。
            ratio: 卖出 K1 数量 / 买入 K2 数量。

        Returns:
            Trade 对象；若无法构建返回 None。
        """
        spread = PutRatioSpread.build(
            future_price=future_price,
            strike_step=self.strike_step,
            strike_half_width=self.strike_half_width,
            interval=interval,
            ratio=ratio,
            option_chain=option_chain,
            contracts_k2=self.contracts_k2,
            entry_date=rebalance_date,
            expiry_date=expiry_date,
            product_prefix=self.product_prefix,
            point_value=self.point_value,
            max_bid_ask_spread_64=self.max_bid_ask_spread_64,
        )
        if spread is None:
            return None
        return Trade(
            entry_date=rebalance_date,
            exit_date=expiry_date,
            spread=spread,
            params_interval=interval,
            params_ratio=ratio,
        )

    def build_option_chain(
        self,
        option_data: Dict[str, Dict[str, Optional[float]]],
        strikes: List[float],
    ) -> Dict[float, Dict[str, Optional[float]]]:
        """
        从截面期权数据构建期权链。

        Args:
            option_data: {ticker: {"bid": float, "ask": float}}。
            strikes: 行权价列表，用于匹配。

        Returns:
            {strike: {"bid": float, "ask": float}}。
        """
        chain: Dict[float, Dict[str, Optional[float]]] = {}
        for s in strikes:
            key = round(s, 6)
            # 简化匹配：假设 option_data 的 key 已经包含 strike
            chain[key] = option_data.get(key, {"bid": None, "ask": None})
        return chain
