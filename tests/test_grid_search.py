"""
网格搜索测试。
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.optimization.grid_search import Episode, GridSearch, ParameterSet


def _make_episode(
    future_entry: float,
    future_expiry: float,
    strikes_and_quotes: dict,
) -> Episode:
    """辅助函数：构造 episode。"""
    return Episode(
        entry_date=dt.date(2020, 1, 1),
        expiry_date=dt.date(2020, 2, 1),
        future_price_entry=future_entry,
        future_price_expiry=future_expiry,
        option_chain=strikes_and_quotes,
    )


def test_grid_search_selects_best_ratio() -> None:
    """
    构造一个 episode：低 ratio 盈利高、高 ratio 亏损大，
    验证网格搜索选择使累计 payoff 最大的参数。
    """
    # 期货从 110 跌到 109.5，K1=110, K2=110.5
    chain = {
        110.0: {"bid": 2.0, "ask": 3.0},
        110.5: {"bid": 4.0, "ask": 5.0},
    }
    ep = _make_episode(110.0, 109.5, chain)

    gs = GridSearch(
        intervals=[0.5],
        ratios=[1.0, 1.5, 2.0],
        strike_step=0.5,
        strike_half_width=1.0,
        contracts_k2=1,
        product_prefix="TY",
        point_value=1000.0,
    )
    result = gs.search([ep])

    # ratio=1 时：payoff = (1*2-5)/64 + max(0.5,0) - 1*max(0,0) = -3/64 + 0.5 ≈ 0.453
    # ratio=1.5 时：payoff = (3-5)/64 + 0.5 - 1.5*0.5 ≈ 0.219
    # ratio=2 时：payoff = (4-5)/64 + 0.5 - 2*0.5 ≈ -0.016
    assert result.best_params.interval == 0.5
    assert result.best_params.ratio == pytest.approx(1.0, abs=0.01)


def test_grid_search_no_valid_parameters() -> None:
    """所有参数都无法构建 spread 时应返回 -inf 分数。"""
    chain = {110.0: {"bid": None, "ask": None}}
    ep = _make_episode(110.0, 109.0, chain)

    gs = GridSearch(
        intervals=[0.5],
        ratios=[1.0],
        strike_step=0.25,
        strike_half_width=1.0,
        contracts_k2=1,
        product_prefix="TY",
        point_value=1000.0,
    )
    result = gs.search([ep])
    assert result.best_score == -float("inf")


def test_grid_search_empty_episodes_raises() -> None:
    """episodes 为空时应抛 ValueError。"""
    gs = GridSearch(
        intervals=[0.5],
        ratios=[1.0],
        strike_step=0.25,
        strike_half_width=1.0,
        contracts_k2=1,
        product_prefix="TY",
    )
    with pytest.raises(ValueError):
        gs.search([])


def test_grid_search_rejects_zero_ratio_candidate() -> None:
    with pytest.raises(ValueError, match="range"):
        GridSearch(
            intervals=[0.5],
            ratios=[0.0, 1.0],
            strike_step=0.25,
            strike_half_width=1.0,
            contracts_k2=10,
            product_prefix="TY",
        )


def test_grid_search_accepts_ratio_2_5() -> None:
    """GridSearch 应接受 ratio=2.5。"""
    gs = GridSearch(
        intervals=[0.5],
        ratios=[2.5],
        strike_step=1.0,
        strike_half_width=1.0,
        contracts_k2=10,
        product_prefix="TY",
    )
    assert 2.5 in gs.ratios


def test_grid_search_rejects_ratio_above_2_5() -> None:
    """GridSearch 应拒绝 ratio>2.5。"""
    with pytest.raises(ValueError, match="range"):
        GridSearch(
            intervals=[0.5],
            ratios=[2.6],
            strike_step=1.0,
            strike_half_width=1.0,
            contracts_k2=10,
            product_prefix="TY",
        )
