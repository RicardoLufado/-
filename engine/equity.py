"""期货权益自动盯市估算（模型估计）。

account.json 里的 futures_equity 视为 updated_at 当天收盘后 App 显示的期货权益。之后每次运行：
    估算权益 = 报告权益 + Σ 方向 × 手数 × (2612 收盘(t0) − 2612 收盘(updated_at)) × 乘数
没有持仓时权益不会变，直接用报告值。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from .config import ContractSpec, to_date


def close_on_or_before(d: pd.DataFrame, day: date) -> tuple[float, date] | None:
    on = d[d["date"] <= pd.Timestamp(day)]
    if len(on) == 0:
        return None
    return float(on["close"].iloc[-1]), on["date"].iloc[-1].date()


def mark_to_market(account: dict, spec: ContractSpec, contract_daily: dict[str, pd.DataFrame], t0: date) -> dict:
    reported = float(account["futures_equity"])
    ref_day = to_date(account["updated_at"])
    out = {"reported": reported, "reported_date": ref_day.isoformat(), "estimate": reported,
           "estimated": False, "adjustments": [], "warnings": [],
           "method": "直接使用 account.json 的期货权益"}
    positions = account.get("positions") or []
    if not positions or t0 <= ref_day:
        return out

    total = 0.0
    for p in positions:
        c = spec.by_code(p["code"])
        if c is None:
            out["warnings"].append(f"持仓 {p['code']} 不是 2612 合约，没有它的行情，期货权益没有对它盯市")
            continue
        d = contract_daily.get(c.product)
        ref = close_on_or_before(d, ref_day) if d is not None else None
        now = close_on_or_before(d, t0) if d is not None else None
        if ref is None or now is None:
            out["warnings"].append(f"{p['code']} 缺少 {ref_day} 或 {t0} 的收盘价，期货权益没有对它盯市")
            continue
        sign = 1 if p["side"] == "long" else -1
        lots = int(p["lots"])
        adj = sign * lots * (now[0] - ref[0]) * c.multiplier
        total += adj
        out["adjustments"].append({"code": p["code"], "side": p["side"], "lots": lots,
                                   "close_ref": ref[0], "close_ref_date": ref[1].isoformat(),
                                   "close_now": now[0], "close_now_date": now[1].isoformat(), "pnl": adj})
    if out["adjustments"]:
        out["estimate"] = reported + total
        out["estimated"] = True
        out["method"] = (f"模型估计：account.json 的权益（{ref_day} 收盘后）+ 持仓按 2612 收盘价盯市到 {t0}"
                         f"（{total:+,.0f} 元）；收盘后可用 App 真实权益校准")
    return out
