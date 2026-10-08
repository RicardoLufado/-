"""基差线性收敛：F_h = S_h × (1 + b₀ × D_h ÷ D₀)。"""
from datetime import date

import numpy as np
import pytest

from engine.model import ModelInputs, build_scenario, equity_futures_path


def test_linear_convergence_endpoints():
    S, b0, D0 = 7000.0, -0.018, 50
    assert equity_futures_path(S, b0, D0, D0) == pytest.approx(S * (1 + b0))   # 今天：F = F0
    assert equity_futures_path(S, b0, 0, D0) == pytest.approx(S)               # 到期：F = S
    mid = equity_futures_path(S, b0, 25, D0)
    assert mid == pytest.approx(S * (1 + b0 / 2))                               # 一半时间收敛一半


def _inputs(z, basis=-0.02, H=10, D0=30):
    S0 = np.array([7000.0, 100.0])
    return ModelInputs(
        products=["IM", "T"], kinds=["equity", "bond"],
        multipliers=np.array([200.0, 10000.0]), margin_rates=np.array([0.15, 0.03]),
        fee_open=np.zeros(2), fee_close=np.zeros(2), z=z, sigma0=np.array([0.015, 0.002]),
        S0=S0, F0=np.array([S0[0] * (1 + basis), 100.0]),
        D=np.arange(D0 - 1, D0 - 1 - H, -1), D0=D0, dates=[date(2026, 10, 9)] * H,
        etf0=500_000.0, bond0=480_000.0, etf_col=0, bond_cols=[1], lam=0.94, block_len=5,
    )


def test_zero_shocks_give_pure_carry():
    z = np.zeros((100, 2))
    mi = _inputs(z)
    sc = build_scenario(mi, n_paths=4, seed=1)
    S0, b0, D0 = 7000.0, -0.02, 30
    expected = S0 * (1 + b0 * mi.D / D0)
    np.testing.assert_allclose(sc.F[0, :, 0], expected)
    # 贴水 → 指数不动时多头每天都有正的 carry
    assert np.all(np.diff(sc.pnl[0, :, 0]) > 0)
    assert sc.pnl[0, -1, 0] == pytest.approx((expected[-1] - S0 * (1 + b0)) * 200)
    # 国债：零冲击则价格不变
    np.testing.assert_allclose(sc.F[0, :, 1], 100.0)
    # 现货不变
    np.testing.assert_allclose(sc.spot, 980_000.0)


def test_basis_ratio_depends_only_on_days_left():
    """随机路径上 F_h ÷ S_h 恒等于 1 + b₀ × D_h ÷ D₀（与指数涨跌无关）。"""
    rng = np.random.default_rng(0)
    z = rng.standard_normal((300, 2))
    mi = _inputs(z)
    mi.bond0 = 0.0          # 现货只剩 ETF → 现货路径 ∝ 指数路径，可以反推 S_h
    sc = build_scenario(mi, n_paths=50, seed=3)
    S = mi.S0[0] * sc.spot / mi.etf0
    expected = np.broadcast_to(1 + (-0.02) * mi.D[None, :] / 30, S.shape)
    np.testing.assert_allclose(sc.F[:, :, 0] / S, expected, rtol=1e-12)
    assert np.std(S[:, -1]) > 0   # 路径确实是随机的
