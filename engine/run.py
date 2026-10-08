"""入口：python -m engine.run

生成 site/data/latest.json 和存档 site/data/history/YYYYMMDD_HHMM.json。
本地测试可用 --synthetic（合成数据，页面会显示红色横幅）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import report
from .config import (BEIJING, ROOT, ContractSpec, load_account, load_contracts, load_strategy,
                     now_beijing, to_date)
from .data import CACHE_DIR, MarketData, fetch_market_data
from .model import (ModelInputs, bond_roll_returns, build_returns, build_scenario,
                    count_trading_days_after, ewma_filter)
from .optimize import search
from .orders import make_orders
from .portfolio import metrics, neg_variance_objective, ramp_objective
from .spot import spot_value_at

SITE_DATA = ROOT / "site" / "data"
MODE_NAMES = {
    "A": ("模式 A：晋级概率最大化", "最大化 P(总金额 ≥ τ)，τ 在晋级线区间内均匀分布"),
    "D": ("模式 D：锁定（最小方差）", "同样的整数枚举，最小化期末总金额的方差"),
    "empty": ("空仓（目前状态）", "不做任何期货交易"),
    "ts1": ("1 手 TS", "只买多 1 手二债2612，满足「至少一笔有效交易」"),
}


def log(msg: str):
    print(msg, flush=True)


def clean_json(x):
    """NaN / inf → None；numpy 类型 → Python 类型。"""
    if isinstance(x, dict):
        return {str(k): clean_json(v) for k, v in x.items() if not str(k).startswith("_")}
    if isinstance(x, (list, tuple)):
        return [clean_json(v) for v in x]
    if isinstance(x, (np.floating, float)):
        x = float(x)
        return x if math.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.ndarray):
        return clean_json(x.tolist())
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    return x


def last_completed_trading_day(calendar: list[date], now: datetime) -> date | None:
    today = now.date()
    past = [d for d in calendar if d < today]
    if today in calendar and (now.hour, now.minute) >= (15, 15):
        return today
    return past[-1] if past else None


def build(spec: ContractSpec, strategy: dict, account: dict, md: MarketData, now: datetime,
          paths_screen: int | None = None, paths_final: int | None = None,
          robustness: bool = True) -> dict:
    t_start = time.monotonic()
    warnings = list(md.warnings)
    mcfg, risk, thr = strategy["model"], strategy["risk"], strategy["threshold"]
    low, high = float(thr["low"]), float(thr["high"])
    final_date = to_date(strategy["competition"]["final_date"])
    paths_screen = int(paths_screen or mcfg["paths_screen"])
    paths_final = int(paths_final or mcfg["paths_final"])
    lam, block_len = float(mcfg["ewma_lambda"]), int(mcfg["block_len"])
    mad_k = float(strategy["data"]["outlier_mad_k"])

    # ---- 历史收益 ----
    all_products = spec.products
    if "IF" not in md.closes:
        raise RuntimeError("沪深300指数历史缺失：无法估值现货 ETF，也无法模拟 IF")
    if "T" not in md.closes and "TF" not in md.closes:
        raise RuntimeError("T 与 TF 主连历史都缺失：无法估值现货债券部分")
    bond_products = [c.product for c in spec.contracts if c.kind == "bond"]
    rets, rinfo = build_returns(md.closes, all_products, to_date(strategy["data"]["history_start"]),
                                bond_products, mad_k)
    products = rinfo["products"]
    if len(rets) < 120:
        raise RuntimeError(f"历史收益只有 {len(rets)} 天，太少")
    t0 = to_date(rinfo["price_last"])
    log(f"历史：{rinfo['price_first']} ~ {rinfo['price_last']}，价格 {rinfo['n_prices']} 天，"
        f"收益 {rinfo['n_returns']} 天，剔除离群 {len(rinfo['removed_outliers'])} 个")
    for p in all_products:
        if p not in products:
            warnings.append(f"{p} 历史数据缺失，本次不参与优化（手数固定为 0）")

    contracts = [spec.by_product(p) for p in products]
    kinds = [c.kind for c in contracts]
    z, sigma_next, _ = ewma_filter(rets[products].values, lam)

    # ---- t0 价格 ----
    S0 = np.array([float(md.closes[p][md.closes[p].index <= pd.Timestamp(t0)].iloc[-1]) for p in products])
    F0 = np.full(len(products), np.nan)
    latest_close = {}
    for k, p in enumerate(products):
        d = md.contract_daily.get(p)
        if d is None or len(d) == 0:
            warnings.append(f"{spec.by_product(p).code} 收盘价数据缺失，本次不参与优化")
            continue
        latest_close[p] = (float(d["close"].iloc[-1]), d["date"].iloc[-1].date())
        on = d[d["date"] <= pd.Timestamp(t0)]
        if len(on) == 0:
            warnings.append(f"{spec.by_product(p).code} 在 {t0} 之前没有收盘价，本次不参与优化")
            continue
        F0[k] = float(on["close"].iloc[-1])
        used = on["date"].iloc[-1].date()
        if used != t0:
            warnings.append(f"{spec.by_product(p).code} 最近收盘日 {used} 早于历史数据日 {t0}，基差可能失真")

    # ---- 日期 ----
    cal = md.calendar
    horizon = [d for d in cal if t0 < d <= final_date]
    if not horizon:
        raise RuntimeError(f"{t0} 之后到 {final_date} 没有交易日（比赛已结束？）")
    D0 = count_trading_days_after(cal, t0, spec.equity_expiry)
    D = np.array([count_trading_days_after(cal, d, spec.equity_expiry) for d in horizon])
    today = now.date()
    market_status = "交易日" if today in set(cal) else "休市"
    lctd = last_completed_trading_day(cal, now)
    if lctd and t0 < lctd and not md.synthetic:
        warnings.append(f"数据截至 {t0}，不是最近一个已收盘交易日 {lctd}：数据源可能还没更新")

    # ---- 现货 ----
    bond_cols_names = [p for p in ("T", "TF") if p in md.closes]
    bond_ret = bond_roll_returns(md.closes, bond_cols_names, mad_k)
    sp = spot_value_at(account, md.closes["IF"], bond_ret, t0)
    if sp["csi300_cost_date"] != account["spot_cost_date"]:
        warnings.append(f"沪深300 在 {account['spot_cost_date']} 没有收盘价，用 {sp['csi300_cost_date']} 代替")
    if sp["asof_used"] != account["spot_value_asof"]:
        warnings.append(f"沪深300 在 {account['spot_value_asof']} 没有收盘价，用 {sp['asof_used']} 代替")
    E0 = float(account["futures_equity"])
    total_now = sp["total"] + E0
    if to_date(account["updated_at"]) < t0:
        warnings.append(f"account.json 更新于 {account['updated_at']}，早于数据日 {t0}：期货权益和持仓请以 App 为准并及时更新")

    mi = ModelInputs(
        products=products, kinds=kinds,
        multipliers=np.array([c.multiplier for c in contracts]),
        margin_rates=np.array([c.margin_rate for c in contracts]),
        fee_open=np.array([c.fee_per_lot(F0[k] if np.isfinite(F0[k]) else S0[k], "open") for k, c in enumerate(contracts)]),
        fee_close=np.array([c.fee_per_lot(F0[k] if np.isfinite(F0[k]) else S0[k], "close") for k, c in enumerate(contracts)]),
        z=z, sigma0=sigma_next, S0=S0, F0=F0, D=D, D0=D0, dates=horizon,
        etf0=sp["etf"], bond0=sp["bond"], etf_col=products.index("IF"),
        bond_cols=[products.index(p) for p in bond_cols_names if p in products],
        lam=lam, block_len=block_len,
    )
    if not mi.bond_cols:
        raise RuntimeError("T 与 TF 都不在收益交集中，无法模拟现货债券部分")

    seed = int(mcfg["seed"])
    sc_screen = build_scenario(mi, paths_screen, seed)
    sc_final = build_scenario(mi, paths_final, seed + 1)
    log(f"情景：{len(horizon)} 个交易日（{horizon[0]} ~ {horizon[-1]}），D0={D0}，"
        f"初筛 {paths_screen} 条 / 终评 {paths_final} 条路径")

    liq = float(risk["liq_ratio"])
    ramp = lambda W: ramp_objective(W, low, high)  # noqa: E731
    resA = search(sc_screen, sc_final, kinds, E0, strategy, ramp, log, "模式 A")
    resD = search(sc_screen, sc_final, kinds, E0, strategy, neg_variance_objective, log, "模式 D")

    K = len(products)
    lots_by_mode = {"A": resA.n, "D": resD.n, "empty": np.zeros(K, dtype=int)}
    if "TS" in products and sc_final.tradable[products.index("TS")]:
        v = np.zeros(K, dtype=int)
        v[products.index("TS")] = 1
        lots_by_mode["ts1"] = v
    else:
        warnings.append("TS 数据缺失，无法计算「1 手 TS」基准")

    # ---- 稳健性：换 3 个种子重算最优解 ----
    rob = None
    if robustness:
        runs = []
        for s in mcfg["robustness_seeds"]:
            s = int(s)
            a = build_scenario(mi, paths_screen, s)
            b = build_scenario(mi, paths_final, s + 1)
            r = search(a, b, kinds, E0, strategy, ramp, log, f"稳健性 seed={s}")
            same = metrics(b, resA.n, E0, liq, low, high)["promotion_prob"]
            runs.append({"seed": s, "lots": dict(zip(products, map(int, r.n))), "promotion_prob": r.score,
                         "recommended_on_this_seed": same})
        probs = [r["promotion_prob"] for r in runs]
        rob = {"runs": runs, "min": min(probs), "max": max(probs),
               "same_solution": all(r["lots"] == dict(zip(products, map(int, resA.n))) for r in runs)}

    # ---- 合约展示信息 ----
    ref_prices: dict[str, float | None] = {}
    contract_rows = []
    vol_annual = dict(zip(products, sigma_next * math.sqrt(252)))
    for c in spec.contracts:
        p = c.product
        row = {"product": p, "code": c.code, "name": c.app_name, "kind": c.kind,
               "index_name": c.history_name, "multiplier": c.multiplier, "margin_rate": c.margin_rate,
               "tick": c.tick, "fee_status": c.fee.get("status")}
        k = products.index(p) if p in products else None
        row["vol_annual"] = float(vol_annual[p]) if p in vol_annual else None
        row["F0"] = float(F0[k]) if k is not None and np.isfinite(F0[k]) else None
        row["S0"] = float(S0[k]) if k is not None else None
        lc = latest_close.get(p)
        row["last_close"], row["last_close_date"] = (lc[0], lc[1].isoformat()) if lc else (None, None)
        q = md.spot_quotes.get(p)
        row["spot_price"], row["spot_time"] = (q["price"], q.get("time")) if q else (None, None)
        ref = row["spot_price"] or row["last_close"]
        row["ref_price"] = c.round_tick(ref) if ref else None
        if row["spot_price"]:
            row["ref_source"] = f"新浪实时行情（{row['spot_time']}）" if row["spot_time"] else "新浪实时行情"
        else:
            row["ref_source"] = "最近收盘价" if ref else "数据缺失"
        row["margin_per_lot"] = c.margin_per_lot(row["ref_price"]) if row["ref_price"] else None
        row["tradable"] = bool(k is not None and sc_final.tradable[k])
        if c.kind == "equity" and row["F0"] and row["S0"]:
            b0 = row["F0"] / row["S0"] - 1
            row["basis"] = b0
            row["basis_points"] = row["F0"] - row["S0"]
            row["basis_annual"] = b0 * 252 / D0
            DH = int(D[-1])
            row["carry_to_end"] = -b0 * row["S0"] * (1 - DH / D0) * c.multiplier
        else:
            row["basis"] = None
        ref_prices[c.code] = row["ref_price"]
        contract_rows.append(row)

    # ---- 各模式输出 ----
    names = {p: spec.by_product(p).app_name for p in products}
    modes_out, W_by_mode = {}, {}
    for mid, n in lots_by_mode.items():
        m = metrics(sc_final, n, E0, liq, low, high)
        W_by_mode[mid] = m.pop("_W")
        lots = {p: int(v) for p, v in zip(products, n)}
        target = {spec.by_product(p).code: v for p, v in lots.items() if v != 0}
        tp = []
        for p, v in lots.items():
            if v == 0:
                continue
            c = spec.by_product(p)
            ref = ref_prices.get(c.code)
            tp.append({"product": p, "code": c.code, "name": c.app_name, "side": "long" if v > 0 else "short",
                       "side_cn": "多" if v > 0 else "空", "lots": abs(v), "ref_price": ref,
                       "margin": c.margin_per_lot(ref) * abs(v) if ref else None})
        title, desc = MODE_NAMES[mid]
        modes_out[mid] = {
            "id": mid, "name": title, "desc": desc, "lots": lots,
            "summary": report.positions_text(lots, names),
            "target_positions": tp,
            "orders": make_orders(account["positions"], target, spec, ref_prices),
            "metrics": m,
        }
    modes_out["A"]["robustness"] = rob
    modes_out["A"]["search"] = {"n_candidates": resA.n_candidates, "n_feasible": resA.n_feasible,
                                "descent_steps": resA.descent_steps, "top": resA.top}
    modes_out["D"]["search"] = {"n_candidates": resD.n_candidates, "n_feasible": resD.n_feasible,
                                "descent_steps": resD.descent_steps}
    for p in account["positions"]:
        if spec.by_code(p["code"]) is None:
            warnings.append(f"当前持仓 {p['code']} 不是 2612 合约，模型没有模拟它；下单指令按目标持仓给出了平仓")

    status = report.classify_status(total_now, low, high)
    recommended = "A"
    if status == "锁定" and (modes_out["D"]["metrics"]["promotion_prob"]
                           >= modes_out["A"]["metrics"]["promotion_prob"] - float(strategy["status"]["lock_tolerance"])):
        recommended = "D"

    ctx = {"total_now": total_now, "spot_now": sp["total"], "E0": E0, "low": low, "high": high,
           "horizon_days": len(horizon), "contracts": contract_rows, "D0": D0, "modes": modes_out,
           "status": status, "removed_outliers": rinfo["removed_outliers"]}

    repo = os.environ.get("GITHUB_REPOSITORY") or strategy["site"]["repo_fallback"]
    return {
        "schema": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "data_asof": t0.isoformat(),
        "market_status": market_status,
        "synthetic": md.synthetic,
        "data_sources": [s.to_dict() for s in md.sources],
        "account": {
            "updated_at": account["updated_at"],
            "futures_equity": E0,
            "positions": account["positions"],
            "spot": {"value": sp["total"], "etf": sp["etf"], "bond": sp["bond"], "asof": t0.isoformat(),
                     "etf_asof": sp["etf_asof"], "bond_asof": sp["bond_asof"],
                     "reported_value": account["spot_value"], "reported_asof": account["spot_value_asof"],
                     "method": "模型估计：ETF 按沪深300涨跌、债券按 T/TF 主连平均收益滚动"},
            "total": total_now,
        },
        "threshold": {"low": low, "high": high, "note": thr.get("note")},
        "status": status,
        "recommended_mode": recommended,
        "horizon": {"start": horizon[0].isoformat(), "end": horizon[-1].isoformat(),
                    "trading_days": len(horizon), "expiry_2612": spec.equity_expiry.isoformat(), "D0": D0,
                    "calendar_source": md.calendar_source},
        "contracts": contract_rows,
        "modes": [modes_out[k] for k in ("A", "D", "empty", "ts1") if k in modes_out],
        "histogram": report.histogram(W_by_mode, low, high),
        "explanations": report.explanations(ctx),
        "limitations": report.limitations(ctx),
        "history_info": rinfo,
        "warnings": warnings,
        "engine": {
            "akshare_version": md.akshare_version,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "paths_screen": paths_screen, "paths_final": paths_final,
            "seed": seed, "robustness_seeds": mcfg["robustness_seeds"],
            "ewma_lambda": lam, "block_len": block_len, "liq_ratio": liq,
            "margin_cap": float(risk["margin_cap"]),
            "runtime_sec": round(time.monotonic() - t_start, 1),
            "repo": repo,
            "run_url": (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/"
                        f"{os.environ['GITHUB_RUN_ID']}") if os.environ.get("GITHUB_RUN_ID") else None,
        },
    }


def write_outputs(result: dict, out_dir: Path, cache_dir: Path | None, now: datetime, keep: int = 300):
    out_dir.mkdir(parents=True, exist_ok=True)
    hist = out_dir / "history"
    hist.mkdir(exist_ok=True)
    payload = json.dumps(clean_json(result), ensure_ascii=False, indent=1, allow_nan=False)
    (out_dir / "latest.json").write_text(payload, encoding="utf-8")
    name = now.strftime("%Y%m%d_%H%M") + ".json"
    (hist / name).write_text(payload, encoding="utf-8")
    if cache_dir is not None:
        good = cache_dir / "last_good"
        good.mkdir(parents=True, exist_ok=True)
        (good / "latest.json").write_text(payload, encoding="utf-8")
        ch = cache_dir / "history"
        ch.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hist / name, ch / name)
    sync_history(out_dir, cache_dir, keep)


def sync_history(out_dir: Path, cache_dir: Path | None, keep: int = 300):
    """Actions 每次都从干净的仓库开始：历史存档放在缓存里，部署前复制回 site/data/history。"""
    hist = out_dir / "history"
    hist.mkdir(parents=True, exist_ok=True)
    if cache_dir is not None and (cache_dir / "history").exists():
        files = sorted((cache_dir / "history").glob("*.json"))
        for old in files[:-keep] if len(files) > keep else []:
            old.unlink()
        for f in sorted((cache_dir / "history").glob("*.json")):
            if not (hist / f.name).exists():
                shutil.copy2(f, hist / f.name)
    names = sorted((f.name for f in hist.glob("*.json") if f.name != "index.json"), reverse=True)[:keep]
    (hist / "index.json").write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成目标持仓与下单指令")
    ap.add_argument("--synthetic", action="store_true", help="用合成数据（只用于测试）")
    ap.add_argument("--out", type=Path, default=SITE_DATA)
    ap.add_argument("--cache", type=Path, default=CACHE_DIR)
    ap.add_argument("--paths-screen", type=int)
    ap.add_argument("--paths-final", type=int)
    ap.add_argument("--no-robustness", action="store_true")
    ap.add_argument("--no-spot", action="store_true", help="不抓盘中最新价")
    ap.add_argument("--now", help="模拟当前北京时间（ISO 格式，测试用）")
    args = ap.parse_args(argv)

    now = datetime.fromisoformat(args.now).replace(tzinfo=BEIJING) if args.now else now_beijing()
    spec, strategy, account = load_contracts(), load_strategy(), load_account()
    log(f"开始：{now.isoformat(timespec='seconds')}（北京时间）")
    if args.synthetic:
        from .synthetic import synthetic_market_data
        md = synthetic_market_data(spec)
    else:
        md = fetch_market_data(spec, strategy, args.cache, log=log, with_spot=not args.no_spot)
    result = build(spec, strategy, account, md, now, args.paths_screen, args.paths_final,
                   robustness=not args.no_robustness)
    write_outputs(result, args.out, None if args.synthetic else args.cache, now,
                  int(strategy["site"]["history_keep"]))
    A = next(m for m in result["modes"] if m["id"] == "A")
    log(f"完成：数据截至 {result['data_asof']}，状态 {result['status']}，推荐 {A['summary']}，"
        f"晋级概率 {A['metrics']['promotion_prob']:.2%}，用时 {result['engine']['runtime_sec']}s")
    for w in result["warnings"]:
        log(f"[警告] {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
