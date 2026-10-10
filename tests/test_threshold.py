"""晋级线（相对口径）：现货互相抵消、人群 β、状态判断。"""
import numpy as np
import pytest

from engine.threshold import INITIAL_FUTURES, Threshold, VarianceObjective, from_config
from tests.helpers import make_scenario


def scenario_with(spot_H, cum_if):
    """3 条路径、1 天、1 个合约（价格不动），只用来提供现货期末值和沪深300 累计收益。"""
    P = len(spot_H)
    F = np.full((P, 1, 1), 4000.0)
    sc = make_scenario(F, F0=[4000.0], mult=[300], margin_rate=[0.15],
                       spot=np.asarray(spot_H, dtype=float)[:, None], spot0=980_000)
    sc.extra = {"cum_H": np.asarray(cum_if, dtype=float)[:, None]}
    return sc


def test_relative_ignores_spot():
    thr = Threshold("relative", 0.0, 60_000.0, [0.0], i_if=0)
    pnl = np.array([30_000.0, 30_000.0, 30_000.0])
    for spot in ([900_000, 980_000, 1_100_000], [980_000] * 3):
        sc = scenario_with(spot, [0.0, 0.0, 0.0])
        W = np.asarray(spot) + INITIAL_FUTURES + pnl
        assert thr.prob(W, sc)[0] == pytest.approx(0.5)      # 现货怎么变，晋级概率都一样


def test_crowd_beta_moves_the_line():
    sc = scenario_with([980_000] * 3, np.log([1.10, 1.0, 0.90]))   # 沪深300 +10% / 0 / −10%
    W = 980_000 + INITIAL_FUTURES + np.full(3, 60_000.0)            # 期货盈利固定 6 万（平仓锁定）
    no_crowd = Threshold("relative", 0.0, 60_000.0, [0.0], i_if=0)
    crowd = Threshold("relative", 0.0, 60_000.0, [1_000_000.0], i_if=0)
    assert no_crowd.prob(W, sc)[0] == pytest.approx(1.0)
    # 人群持有 100 万多头：大盘 +10% 时线上移 10 万 → 这条路径晋级概率 0
    expected = np.mean([0.0, 1.0, 1.0])
    assert crowd.prob(W, sc)[0] == pytest.approx(expected)
    both = Threshold("relative", 0.0, 60_000.0, [0.0, 1_000_000.0], i_if=0)
    assert both.prob(W, sc)[0] == pytest.approx((1.0 + expected) / 2)


def test_status_and_position():
    thr = Threshold("relative", 0.0, 60_000.0, [0.0], i_if=0)
    assert thr.status(total_now=0, E0=INITIAL_FUTURES - 1) == "追赶"
    assert thr.status(total_now=0, E0=INITIAL_FUTURES + 30_000) == "持平"
    assert thr.status(total_now=0, E0=INITIAL_FUTURES + 63_985) == "锁定"
    absolute = Threshold("absolute", 2_080_000, 2_160_000)
    assert absolute.status(total_now=2_043_223, E0=1_063_984) == "追赶"


def test_from_config_and_variance_objective():
    thr = from_config({"mode": "relative", "excess_low": 0, "excess_high": 60000, "crowd_if_lots": [0, 1]},
                      ["IF", "IM"], if_lot_value=1_273_440)
    assert thr.relative and thr.crowd_notional == [0.0, 1_273_440.0] and thr.i_if == 0
    sc = scenario_with([900_000, 1_000_000, 1_100_000], [0, 0, 0])
    W = np.array([[1_900_000.0], [2_000_000.0], [2_100_000.0]])   # 期货权益恒为 100 万，波动全来自现货
    assert VarianceObjective(True)(W, sc)[0] == pytest.approx(0.0)
    assert VarianceObjective(False)(W, sc)[0] < 0


# ---- 端到端：build() 在两种口径下的行为 ----

from datetime import datetime  # noqa: E402

from engine.config import BEIJING, load_contracts, load_strategy  # noqa: E402
from engine.run import build  # noqa: E402
from engine.synthetic import synthetic_market_data  # noqa: E402
from tests.helpers import base_account  # noqa: E402


def _run(strategy, account):
    spec = load_contracts()
    md = synthetic_market_data(spec)
    return build(spec, strategy, account, md, datetime(2026, 10, 9, 8, 45, tzinfo=BEIJING),
                 paths_screen=200, paths_final=600, robustness=False)


def test_relative_mode_end_to_end_lock_when_ahead():
    strategy = load_strategy()
    strategy["threshold"]["mode"] = "relative"
    acc = dict(base_account(), futures_equity=1_063_984.58)       # 期货领先 +6.4 万 ≥ 上沿 6 万
    res = _run(strategy, acc)
    thr = res["threshold"]
    assert thr["mode"] == "relative" and res["status"] == "锁定"
    assert thr["position"] == pytest.approx(63_984.58)
    spot = res["account"]["spot"]["value"]
    assert thr["band_total_low"] == pytest.approx(spot + INITIAL_FUTURES + thr["low"])
    assert thr["band_total_high"] == pytest.approx(spot + INITIAL_FUTURES + thr["high"])
    A = next(m for m in res["modes"] if m["id"] == "A")
    E = next(m for m in res["modes"] if m["id"] == "empty")
    assert A["metrics"]["promotion_prob"] >= E["metrics"]["promotion_prob"] - 0.01
    assert any("名次只看期货账户" in x for x in res["explanations"])


def test_relative_mode_chasing_when_behind():
    strategy = load_strategy()
    strategy["threshold"]["mode"] = "relative"
    res = _run(strategy, dict(base_account(), futures_equity=950_000.0))   # 期货亏 5 万
    assert res["status"] == "追赶"


def test_absolute_mode_still_supported():
    strategy = load_strategy()
    strategy["threshold"]["mode"] = "absolute"
    res = _run(strategy, base_account())
    assert res["threshold"]["mode"] == "absolute"
    assert res["threshold"]["band_total_low"] == strategy["threshold"]["low"]
    assert res["status"] == "追赶"        # 198 万 < 208 万
