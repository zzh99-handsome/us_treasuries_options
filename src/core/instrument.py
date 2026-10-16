"""
期权合约与 Put Ratio Spread 结构定义。

Put Ratio Spread 结构：
- 卖出 ratio 张低行权价 Put（Strike1 / K1）
- 买入 1 张高行权价 Put（Strike2 / K2）
- 权利金与到期盈亏均以 64 分之一（1/64）报价习惯计算
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd


@dataclass(frozen=True)
class OptionContract:
    """单个期权合约。"""

    ticker: str
    option_type: str  # "P" or "C"
    strike: float
    expiry_date: dt.date


@dataclass
class PutRatioSpread:
    """
    Put Ratio Spread 持仓。

    Attributes:
        k1: 低行权价 Put 的 strike。
        k2: 高行权价 Put 的 strike。
        ratio: 卖出 K1 Put 数量 / 买入 K2 Put 数量。
        contracts_k2: 买入 K2 Put 的合约张数。
        entry_date: 建仓日期。
        expiry_date: 到期日期。
        low_put: 低行权价 Put 合约对象。
        high_put: 高行权价 Put 合约对象。
        bid_k1_64: 建仓时 K1 Put 的 bid（64 分之一）。
        ask_k2_64: 建仓时 K2 Put 的 ask（64 分之一）。
        point_value: 每点对应的美元金额。
    """

    k1: float
    k2: float
    ratio: float
    contracts_k2: int
    entry_date: dt.date
    expiry_date: dt.date
    low_put: OptionContract
    high_put: OptionContract
    bid_k1_64: float
    ask_k2_64: float
    point_value: float = 1000.0
    bid_k1_timestamp: Optional[dt.datetime] = None
    ask_k2_timestamp: Optional[dt.datetime] = None
    bid_k1_quote_date: Optional[dt.date] = None
    ask_k2_quote_date: Optional[dt.date] = None

    @property
    def buy_qty(self) -> int:
        """买入高行权价 Put 的总张数。"""
        return self.contracts_k2

    @property
    def sell_qty(self) -> int:
        """卖出低行权价 Put 的总张数。"""
        return int(round(self.ratio * self.contracts_k2))

    @property
    def premium_cashflow(self) -> float:
        """入场净权利金现金流（美元；收取为正、支付为负）。"""
        return self.sell_premium_cashflow + self.buy_premium_cashflow

    @property
    def sell_premium_cashflow(self) -> float:
        """卖出腿 Bid 权利金现金流（美元，正值）。"""
        return self.sell_qty * self.bid_k1_64 / 64.0 * self.point_value

    @property
    def buy_premium_cashflow(self) -> float:
        """买入腿 Ask 权利金现金流（美元，负值）。"""
        return -self.buy_qty * self.ask_k2_64 / 64.0 * self.point_value

    def settlement_cashflow(self, future_price: float) -> float:
        """到期各腿内在价值现金流（美元）。"""
        bought = self.buy_qty * max(self.k2 - future_price, 0.0)
        sold = self.sell_qty * max(self.k1 - future_price, 0.0)
        return (bought - sold) * self.point_value

    @property
    def net_premium_points(self) -> float:
        """
        建仓时的净权利金（期货点）。

        正值表示收权利金，负值表示付权利金。
        """
        # 卖出 ratio 张 K1 Put 收入 +ratio * bid_k1 / 64
        # 买入 1 张 K2 Put 支出 -ask_k2 / 64
        return (self.sell_qty * self.bid_k1_64 - self.buy_qty * self.ask_k2_64) / (
            64.0 * self.buy_qty
        )

    @property
    def net_premium_dollars(self) -> float:
        """建仓时的净权利金（美元）。"""
        return self.premium_cashflow

    def payoff_at_expiration(self, future_price: float) -> float:
        """
        到期时按单张 K2 买入腿计算的 payoff（期货点）。

        Args:
            future_price: 到期时的期货价格。

        Returns:
            期货点收益。
        """
        return self.net_premium_points + max(self.k2 - future_price, 0.0) - (
            self.sell_qty / self.buy_qty
        ) * max(self.k1 - future_price, 0.0)

    def realized_pnl(self, future_price_at_expiry: float) -> float:
        """
        持有到期的实现盈亏（美元）。

        Args:
            future_price_at_expiry: 到期时期货价格。

        Returns:
            美元盈亏。
        """
        return self.premium_cashflow + self.settlement_cashflow(future_price_at_expiry)

    def theoretical_value(self, future_price: float) -> float:
        """
        根据当前期货价计算 spread 的理论价值（期货点）。

        使用剩余期限未到期的内在价值近似，不考虑时间价值与波动率。
        """
        return self.payoff_at_expiration(future_price)

    def daily_pnl(self, future_price: float) -> float:
        """
        按单日期货价格计算 mark-to-market 未实现盈亏（美元）。

        Args:
            future_price: 当前期货价格。

        Returns:
            未实现盈亏。
        """
        return (
            self.theoretical_value(future_price) - self.net_premium_points
        ) * self.contracts_k2 * self.point_value

    def daily_pnl_series(
        self,
        future_prices: pd.Series,
    ) -> pd.Series:
        """
        按日 mark-to-market 的未实现盈亏序列（美元）。

        Args:
            future_prices: 以日期为索引的期货价格序列。

        Returns:
            以日期为索引的日累计盈亏序列。
        """
        values = future_prices.apply(self.theoretical_value)
        pnl = (values - self.net_premium_points) * self.contracts_k2 * self.point_value
        return pd.Series(pnl, index=future_prices.index)

    @classmethod
    def build(
        cls,
        future_price: float,
        strike_step: float,
        strike_half_width: float,
        interval: float,
        ratio: float,
        option_chain: Dict[float, Dict[str, Optional[float]]],
        contracts_k2: int,
        entry_date: dt.date,
        expiry_date: dt.date,
        product_prefix: str,
        point_value: float = 1000.0,
        max_bid_ask_spread_64: Optional[float] = None,
    ) -> Optional["PutRatioSpread"]:
        """
        根据当前期货价与参数构建 Put Ratio Spread。

        Args:
            future_price: 当前期货价格，用于确定行权价网格中心。
            strike_step: 行权价步长。
            strike_half_width: 行权价半宽。
            interval: K2 - K1 的间距。
            ratio: 卖出 K1 数量 / 买入 K2 数量。
            option_chain: {strike: {"bid": float, "ask": float}}。
            contracts_k2: 买入 K2 Put 的张数。
            entry_date: 建仓日期。
            expiry_date: 到期日期。
            product_prefix: 期权代码前缀，用于构造 ticker。
            point_value: 每点美元。

        Returns:
            成功构建返回 PutRatioSpread，否则返回 None。
        """
        if (
            interval <= 0
            or not math.isfinite(ratio)
            or ratio <= 0
            or ratio > 2.5
            or contracts_k2 <= 0
            or point_value <= 0
            or (max_bid_ask_spread_64 is not None and max_bid_ask_spread_64 < 0)
        ):
            return None
        sell_qty = ratio * contracts_k2
        if not math.isclose(sell_qty, round(sell_qty), abs_tol=1e-9):
            return None

        # 以期货现价为中心构建行权价网格
        center = round(future_price / strike_step) * strike_step
        n = int(round(strike_half_width / strike_step))
        strikes = [round(center + i * strike_step, 6) for i in range(-n, n + 1)]

        # K2 = K1 + interval；我们遍历 K2，要求 K1 = K2 - interval 在网格中且有报价
        available = {round(s, 6): s for s in strikes if s in option_chain}
        valid_pairs = []
        for k2 in sorted(available):
            k1 = round(k2 - interval, 6)
            if ratio > 0 and k1 not in available:
                continue
            q1 = option_chain.get(k1, {})
            q2 = option_chain[k2]
            ask_k2 = q2.get("ask")
            try:
                ask_k2 = float(ask_k2)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(ask_k2) or ask_k2 <= 0:
                continue
            ask_width = None
            if ratio > 0:
                try:
                    bid_k1 = float(q1.get("bid"))
                    ask_k1 = float(q1.get("ask"))
                    bid_k2 = float(q2.get("bid"))
                    if any(not math.isfinite(v) or v <= 0 for v in (bid_k1, ask_k1, bid_k2)):
                        continue
                except (TypeError, ValueError):
                    continue
                if bid_k1 > ask_k1 or bid_k2 > ask_k2:
                    continue
                widths = (ask_k1 - bid_k1, ask_k2 - bid_k2)
                if max_bid_ask_spread_64 is not None and any(
                    width > max_bid_ask_spread_64 for width in widths
                ):
                    continue
                ask_width = sum(widths)
            elif max_bid_ask_spread_64 is not None:
                # A pure long put needs an executable Ask; the unused short leg
                # does not impose a bid/ask liquidity requirement.
                try:
                    bid_k2 = float(q2.get("bid"))
                except (TypeError, ValueError):
                    bid_k2 = None
                if bid_k2 is not None and math.isfinite(bid_k2) and bid_k2 > 0:
                    if bid_k2 > ask_k2 or ask_k2 - bid_k2 > max_bid_ask_spread_64:
                        continue
                elif q2.get("bid") not in (None, 0, 0.0):
                    continue
                ask_width = (
                    ask_k2 - bid_k2
                    if bid_k2 is not None and math.isfinite(bid_k2) and bid_k2 > 0
                    else 0.0
                )

            low_put = OptionContract(
                ticker=str(q1.get("ticker", f"{product_prefix} {k1}P")),
                option_type="P",
                strike=k1,
                expiry_date=expiry_date,
            )
            high_put = OptionContract(
                ticker=str(q2.get("ticker", f"{product_prefix} {k2}P")),
                option_type="P",
                strike=k2,
                expiry_date=expiry_date,
            )
            # Prefer the pair with the tightest combined displayed market,
            # then the strike closest to the futures price (stable tie-break).
            valid_pairs.append((
                ask_width if ask_width is not None else float("inf"),
                abs(k2 - future_price),
                k1,
                k2,
                low_put,
                high_put,
                float(q1.get("bid") or 0.0),
                ask_k2,
                q1,
                q2,
            ))
        if not valid_pairs:
            return None
        _, _, k1, k2, low_put, high_put, bid_k1, ask_k2, q1, q2 = min(valid_pairs)
        return cls(
            k1=k1,
            k2=k2,
            ratio=ratio,
            contracts_k2=contracts_k2,
            entry_date=entry_date,
            expiry_date=expiry_date,
            low_put=low_put,
            high_put=high_put,
            bid_k1_64=bid_k1,
            ask_k2_64=ask_k2,
            point_value=point_value,
            bid_k1_timestamp=q1.get("quote_timestamp"),
            ask_k2_timestamp=q2.get("quote_timestamp"),
            bid_k1_quote_date=q1.get("quote_date"),
            ask_k2_quote_date=q2.get("quote_date"),
        )


def build_option_chain(
    strikes: List[float],
    bid_ask_map: Dict[float, Dict[str, Optional[float]]],
) -> Dict[float, Dict[str, Optional[float]]]:
    """
    将行权价与 bid/ask 数据组装为期权链字典。

    Args:
        strikes: 行权价列表。
        bid_ask_map: {strike: {"bid": float, "ask": float}}。

    Returns:
        标准化的期权链字典。
    """
    chain: Dict[float, Dict[str, Optional[float]]] = {}
    for s in strikes:
        chain[round(s, 6)] = bid_ask_map.get(s, {"bid": None, "ask": None})
    return chain
