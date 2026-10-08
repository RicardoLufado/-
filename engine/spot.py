"""现货账户估值（固定持仓，不能交易）。

ETF 部分 = 500,000 × 沪深300收盘(t) ÷ 沪深300收盘(spot_cost_date)        （近似：忽略跟踪误差和分红）
债券部分 = spot_value − ETF 部分（都取 spot_value_asof 那天），之后按 T 与 TF 主连日收益平均值滚动
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

ETF_NOTIONAL = 500_000.0


def value_on(s: pd.Series, d: date) -> tuple[float, date]:
    """取 d 当天（没有则取之前最近一天）的值。"""
    s = s[s.index <= pd.Timestamp(d)]
    if len(s) == 0:
        raise ValueError(f"{d} 之前没有数据")
    return float(s.iloc[-1]), s.index[-1].date()


def split_spot(spot_value: float, csi_cost: float, csi_asof: float,
               etf_notional: float = ETF_NOTIONAL) -> tuple[float, float]:
    etf = etf_notional * csi_asof / csi_cost
    return etf, spot_value - etf


def spot_value_at(account: dict, csi300: pd.Series, bond_ret: pd.Series, t: date) -> dict:
    cost_date = date.fromisoformat(account["spot_cost_date"])
    asof = date.fromisoformat(account["spot_value_asof"])
    csi_cost, cost_used = value_on(csi300, cost_date)
    csi_asof, asof_used = value_on(csi300, asof)
    csi_t, t_used = value_on(csi300, t)
    etf_asof, bond_asof = split_spot(float(account["spot_value"]), csi_cost, csi_asof)
    cum = bond_ret.sort_index().cumsum() if len(bond_ret) else pd.Series(dtype=float)
    if len(cum):
        cum.index = pd.to_datetime(cum.index)

    def cum_at(d: date) -> float:
        if len(cum) == 0:
            return 0.0
        c = cum[cum.index <= pd.Timestamp(d)]
        return float(c.iloc[-1]) if len(c) else 0.0

    etf_t = ETF_NOTIONAL * csi_t / csi_cost
    bond_t = bond_asof * float(np.exp(cum_at(t) - cum_at(asof)))
    return {
        "etf": etf_t,
        "bond": bond_t,
        "total": etf_t + bond_t,
        "etf_asof": etf_asof,
        "bond_asof": bond_asof,
        "csi300_cost": csi_cost,
        "csi300_cost_date": cost_used.isoformat(),
        "csi300_asof": csi_asof,
        "csi300_t": csi_t,
        "asof_used": asof_used.isoformat(),
        "t_used": t_used.isoformat(),
    }
