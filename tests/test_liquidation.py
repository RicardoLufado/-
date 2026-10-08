"""强平与穿仓：当天收盘全部平仓、此后权益冻结；负权益保留不截断。"""
import numpy as np
import pytest

from engine.portfolio import evaluate, metrics
from tests.helpers import make_scenario

E0 = 300_000.0
MULT, RATE, F0 = 300.0, 0.15, 4000.0   # 1 手保证金 18 万


def scenario():
    F = np.array([
        [[4000.0], [4010.0], [4020.0], [4030.0]],   # 路径 0：小涨，不爆仓
        [[3900.0], [3600.0], [3700.0], [4500.0]],   # 路径 1：第 2 天跌破保证金，之后反弹也拿不回
        [[3950.0], [2900.0], [3000.0], [3100.0]],   # 路径 2：第 2 天跳空到负权益（穿仓）
    ])
    return make_scenario(F, F0=[F0], mult=[MULT], margin_rate=[RATE], spot0=1_000_000.0)


def test_liquidation_freezes_equity():
    r = evaluate(scenario(), np.array([[1]]), E0=E0, liq_ratio=1.0)
    # 路径 0：小涨，不爆仓
    assert not r.liquidated[0, 0]
    assert r.E_final[0, 0] == pytest.approx(E0 + 30 * MULT)
    assert r.stop_day[0, 0] == -1
    # 路径 1：第 2 天 E = 180000 > 保证金 3600×300×0.15 = 162000，liq_ratio=1 时不爆仓，期末反弹算数
    assert not r.liquidated[1, 0]
    assert r.E_final[1, 0] == pytest.approx(E0 + 500 * MULT)


def test_liquidation_day_and_frozen_value():
    sc = scenario()
    n = 1
    E = E0 + n * (sc.F[1, :, 0] - F0) * MULT                 # 路径 1 的权益
    M = n * sc.F[1, :, 0] * MULT * RATE
    # 用 liq_ratio 让第 2 天（下标 1）成为第一次 E < M × liq
    liq = float((E[1] / M[1]) + 1e-6)
    assert E[0] >= liq * M[0]
    r = evaluate(sc, np.array([[n]]), E0=E0, liq_ratio=liq)
    assert r.liquidated[1, 0]
    assert r.stop_day[1, 0] == 1
    assert r.E_final[1, 0] == pytest.approx(E[1])            # 冻结在爆仓日，之后反弹不算
    assert r.W[1, 0] == pytest.approx(1_000_000 + E[1])


def test_wipeout_keeps_negative_equity():
    sc = scenario()
    r = evaluate(sc, np.array([[1]]), E0=E0, liq_ratio=1.0)
    E_day1 = E0 + (2900 - 4000) * MULT                      # = −30000
    assert E_day1 < 0
    assert r.liquidated[2, 0] and r.wiped[2, 0]
    assert r.stop_day[2, 0] == 1
    assert r.E_final[2, 0] == pytest.approx(E_day1)          # 负权益保留，不截断为 0
    assert r.W[2, 0] == pytest.approx(1_000_000 + E_day1)


def test_short_position_liquidation():
    F = np.array([[[4000.0], [4500.0], [5200.0]]])
    sc = make_scenario(F, F0=[F0], mult=[MULT], margin_rate=[RATE])
    r = evaluate(sc, np.array([[-1]]), E0=E0, liq_ratio=1.0)
    # 第 1 天：E = 300000 − 500×300 = 150000 < 4500×300×0.15 = 202500 → 爆仓，未穿仓
    assert r.liquidated[0, 0] and not r.wiped[0, 0]
    assert r.stop_day[0, 0] == 1
    assert r.E_final[0, 0] == pytest.approx(150_000)


def test_metrics_probabilities():
    m = metrics(scenario(), np.array([1]), E0=E0, liq_ratio=1.0, low=1_200_000, high=1_400_000)
    assert m["wipeout_prob"] == pytest.approx(1 / 3)
    assert m["liquidation_prob"] >= m["wipeout_prob"]
    assert m["margin_used"] == pytest.approx(F0 * MULT * RATE)
    assert m["margin_ratio"] == pytest.approx(F0 * MULT * RATE / E0)


def test_empty_portfolio_never_liquidates():
    r = evaluate(scenario(), np.array([[0]]), E0=E0, liq_ratio=1.0)
    assert not r.liquidated.any()
    np.testing.assert_allclose(r.E_final, E0)
