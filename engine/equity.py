"""期货权益自动盯市估算（模型估计）。

account.json 里的 futures_equity 视为 updated_at 当天 App 显示的期货权益：
- 没有 mark_prices：视为收盘后抄的，参照价 = updated_at 当天的 2612 收盘价；
- 有 mark_prices（{"IM2612": 6918.4}）：盘中抄的，参照价 = 抄权益时 App 显示的「现价」。
每次运行：
    估算权益 = 报告权益 + Σ 方向 × 手数 × (当前价 − 参照价) × 乘数
当前价 = 盘中运行时用新浪实时价（live），否则用 t0 收盘价。没有持仓时直接用报告值。
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


def mark_to_market(account: dict, spec: ContractSpec, contract_daily: dict[str, pd.DataFrame], t0: date,
                   live: dict[str, float] | None = None, today: date | None = None) -> dict:
    """live：盘中运行时各品种的实时价（产品 -> 价格），此时「当前」是 today 而不是 t0。"""
    reported = float(account["futures_equity"])
    ref_day = to_date(account["updated_at"])
    marks = account.get("mark_prices") or {}
    as_of = today if live is not None and today is not None else t0
    out = {"reported": reported, "reported_date": ref_day.isoformat(), "estimate": reported,
           "estimated": False, "adjustments": [], "warnings": [],
           "method": "直接使用 account.json 的期货权益"}
    positions = account.get("positions") or []
    if not positions:
        return out
    if as_of < ref_day:
        out["warnings"].append(f"行情只到 {as_of}，早于 account.json 的更新日 {ref_day}：期货权益直接用你填写的值")
        return out
    if as_of == ref_day and not marks:
        return out

    total = 0.0
    for p in positions:
        c = spec.by_code(p["code"])
        if c is None:
            out["warnings"].append(f"持仓 {p['code']} 不是 2612 合约，没有它的行情，期货权益没有对它盯市")
            continue
        d = contract_daily.get(c.product)
        if p["code"] in marks:
            ref = (float(marks[p["code"]]), f"{ref_day} 抄权益时的现价")
        else:
            r = close_on_or_before(d, ref_day) if d is not None else None
            ref = (r[0], r[1].isoformat()) if r else None
        if live is not None:
            q = live.get(c.product)
            now = (float(q), f"{as_of} 盘中") if q else None
        else:
            r = close_on_or_before(d, t0) if d is not None else None
            now = (r[0], r[1].isoformat()) if r else None
        if ref is None or now is None:
            out["warnings"].append(f"{p['code']} 缺少参照价或当前价，期货权益没有对它盯市")
            continue
        sign = 1 if p["side"] == "long" else -1
        lots = int(p["lots"])
        adj = sign * lots * (now[0] - ref[0]) * c.multiplier
        total += adj
        out["adjustments"].append({"code": p["code"], "side": p["side"], "lots": lots,
                                   "close_ref": ref[0], "close_ref_date": ref[1],
                                   "close_now": now[0], "close_now_date": now[1], "pnl": adj})
    if out["adjustments"]:
        out["estimate"] = reported + total
        out["estimated"] = True
        src = "新浪实时价" if live is not None else "2612 收盘价"
        out["method"] = (f"模型估计：account.json 的权益（{ref_day}）+ 持仓按{src}盯市到 {as_of}"
                         f"（{total:+,.0f} 元）；可用 App 真实权益校准")
    return out
