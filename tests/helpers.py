"""测试用的小工具：手工构造情景（全部合成数据）。"""
from __future__ import annotations

import numpy as np

from engine.model import Scenario


def base_account() -> dict:
    """测试专用账户（开赛初始状态），不读真实的 state/account.json。"""
    return {
        "updated_at": "2026-10-08",
        "futures_equity": 1000000.00,
        "positions": [],
        "spot_value": 984052.96,
        "spot_value_asof": "2026-09-30",
        "registration_date": "2026-09-20",
        "spot_cost_date": "2026-09-18",
    }


def make_scenario(F: np.ndarray, F0, mult, margin_rate, spot=None, spot0=0.0, fee_open=None, fee_close=None):
    """F: (P, H, K) 价格路径。"""
    F = np.asarray(F, dtype=float)
    P, H, K = F.shape
    F0 = np.asarray(F0, dtype=float)
    mult = np.asarray(mult, dtype=float)
    rate = np.asarray(margin_rate, dtype=float)
    pnl = (F - F0) * mult
    margin = F * mult * rate
    if spot is None:
        spot = np.full((P, H), float(spot0))
    return Scenario(
        products=[f"P{k}" for k in range(K)], dates=list(range(H)), F0=F0, F=F, pnl=pnl, margin=margin,
        spot=np.asarray(spot, dtype=float), spot0=float(spot0),
        fee_open=np.zeros(K) if fee_open is None else np.asarray(fee_open, dtype=float),
        fee_close=np.zeros(K) if fee_close is None else np.asarray(fee_close, dtype=float),
        margin0=F0 * mult * rate, tradable=np.ones(K, dtype=bool),
    )
