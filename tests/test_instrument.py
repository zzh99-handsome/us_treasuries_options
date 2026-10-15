"""
Instrument 层测试：OptionContract、PutRatioSpread 计算。
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.core.instrument import OptionContract, PutRatioSpread


@pytest.fixture
def sample_spread() -> PutRatioSpread:
    """构造一个 K1=110, K2=111, ratio=2 的 Put Ratio Spread。"""
    low_put = OptionContract(ticker="TYZ6P 110", option_type="P", strike=110.0, expiry_date=dt.date(2026, 11, 27))
    high_put = OptionContract(ticker="TYZ6P 111", option_type="P", strike=111.0, expiry_date=dt.date(2026, 11, 27))
    return PutRatioSpread(
        k1=110.0,
        k2=111.0,
        ratio=2.0,
        contracts_k2=1,
        entry_date=dt.date(2026, 10, 15),
        expiry_date=dt.date(2026, 11, 27),
        low_put=low_put,
        high_put=high_put,
        bid_k1_64=2.0,
        ask_k2_64=4.0,
        point_value=1000.0,
    )


def test_net_premium_calculation(sample_spread: PutRatioSpread) -> None:
    """净权利金 = (2 * 2 - 4) / 64 = 0 点。"""
    assert sample_spread.net_premium_points == pytest.approx(0.0)


def test_payoff_above_k2(sample_spread: PutRatioSpread) -> None:
    """F > K2 时，两个 put 均无内在价值，payoff 等于净权利金。"""
    assert sample_spread.payoff_at_expiration(112.0) == pytest.approx(0.0)


def test_payoff_between_k1_and_k2(sample_spread: PutRatioSpread) -> None:
    """K1 < F < K2 时，只有 K2 put 有价值。"""
    # F = 110.5, payoff = 0 + 0.5 - 0 = 0.5
    assert sample_spread.payoff_at_expiration(110.5) == pytest.approx(0.5)


def test_payoff_below_k1(sample_spread: PutRatioSpread) -> None:
    """F < K1 时，两个 put 均有价值，ratio 腿亏损被放大。"""
    # F = 109, payoff = 0 + (111-109) - 2*(110-109) = 2 - 2 = 0
    assert sample_spread.payoff_at_expiration(109.0) == pytest.approx(0.0)


def test_payoff_far_below_k1(sample_spread: PutRatioSpread) -> None:
    """F 远低于 K1 时，ratio 腿亏损主导。"""
    # F = 108, payoff = 0 + 3 - 2*2 = -1
    assert sample_spread.payoff_at_expiration(108.0) == pytest.approx(-1.0)


def test_realized_pnl_dollar_conversion(sample_spread: PutRatioSpread) -> None:
    """payoff 点按 point_value 换算为美元。"""
    pnl = sample_spread.realized_pnl(112.0)
    assert pnl == pytest.approx(0.0)

    pnl = sample_spread.realized_pnl(108.0)
    assert pnl == pytest.approx(-1000.0)


def test_put_ratio_spread_build_success() -> None:
    """用有效参数成功构建 spread。"""
    chain = {
        109.75: {"bid": 1.0, "ask": 2.0},
        110.0: {"bid": 2.0, "ask": 3.0},
        110.25: {"bid": 3.0, "ask": 4.0},
        110.5: {"bid": 4.0, "ask": 5.0},
    }
    spread = PutRatioSpread.build(
        future_price=110.0,
        strike_step=0.25,
        strike_half_width=1.0,
        interval=0.5,
        ratio=2.0,
        option_chain=chain,
        contracts_k2=1,
        entry_date=dt.date(2026, 10, 15),
        expiry_date=dt.date(2026, 11, 27),
        product_prefix="TY",
        point_value=1000.0,
    )
    assert spread is not None
    assert spread.k2 - spread.k1 == pytest.approx(0.5)
    assert spread.ratio == pytest.approx(2.0)


def test_put_ratio_spread_build_invalid_parameters() -> None:
    """无效参数（interval <= 0 或 ratio <= 0）应返回 None。"""
    chain = {110.0: {"bid": 1.0, "ask": 2.0}, 110.5: {"bid": 2.0, "ask": 3.0}}
    assert PutRatioSpread.build(
        future_price=110.0, strike_step=0.25, strike_half_width=1.0,
        interval=-0.5, ratio=2.0, option_chain=chain, contracts_k2=1,
        entry_date=dt.date(2026, 10, 15), expiry_date=dt.date(2026, 11, 27),
        product_prefix="TY",
    ) is None
