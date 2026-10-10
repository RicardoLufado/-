"""相对晋级线口径下的方案对比（只打印结果，不改网页的推荐设置）。

口径：大家的现货都一样，涨跌互相抵消，排名只看期货账户。
    你的期货盈亏   PnL = 期末期货权益 − 1,000,000
    第 3000 名的期货盈亏 C = c0 + Nc × 沪深300 到 11-20 的涨跌幅
      c0 ~ 均匀分布 [lo, hi]（基础值，来自榜单拟合）
      Nc = 人群平均持有的多头市值（「群体 β」，未知，分几档看）
    晋级概率 = P(PnL ≥ C) = mean(clip((PnL − Nc×r − lo)/(hi − lo), 0, 1))

python scripts/threshold_scenarios.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import load_account, load_contracts, load_strategy, now_beijing  # noqa: E402
from engine.data import CACHE_DIR, fetch_market_data  # noqa: E402
from engine.optimize import search  # noqa: E402
from engine.portfolio import evaluate  # noqa: E402
from engine.run import build  # noqa: E402

BANDS = {"基准 0–6 万": (0.0, 60_000.0), "保守 3–9 万": (30_000.0, 90_000.0)}
INITIAL_FUTURES = 1_000_000.0


def rel_prob(pnl: np.ndarray, r_csi: np.ndarray, nc: float, lo: float, hi: float) -> np.ndarray:
    """pnl: (P, B)；返回每个方案的晋级概率 (B,)。"""
    return np.clip((pnl - nc * r_csi[:, None] - lo) / (hi - lo), 0.0, 1.0).mean(axis=0)


def make_objective(nc: float, lo: float, hi: float, i_if: int):
    def obj(W, sc):
        r = np.exp(sc.extra["cum_H"][:, i_if]) - 1.0
        pnl = W - sc.spot[:, -1][:, None] - INITIAL_FUTURES
        return rel_prob(pnl, r, nc, lo, hi)
    obj.needs_scenario = True
    return obj


def lots_text(n: np.ndarray, products: list[str]) -> str:
    parts = [f"{p}{'多' if v > 0 else '空'}{abs(int(v))}" for p, v in zip(products, n) if v != 0]
    return " ".join(parts) if parts else "空仓"


def main() -> int:
    spec, strategy, account = load_contracts(), load_strategy(), load_account()
    now = now_beijing()
    if "--synthetic" in sys.argv:            # 本地自测用：合成数据，数字没有市场含义
        from engine.synthetic import synthetic_market_data
        md = synthetic_market_data(spec, end=datetime(2026, 10, 9).date())
        print("!! 合成数据，仅测试脚本，数字没有市场含义")
    else:
        md = fetch_market_data(spec, strategy, CACHE_DIR, log=lambda *_: None, with_spot=False)
    keep: dict = {}
    build(spec, strategy, account, md, now, robustness=False, keep=keep)
    sc, sc_screen = keep["sc_final"], keep["sc_screen"]
    products, kinds, E0 = keep["products"], keep["kinds"], keep["E0"]
    i_if, i_im = products.index("IF"), products.index("IM")
    if_lot = float(sc.F0[i_if] * spec.by_product("IF").multiplier)
    r_csi = np.exp(sc.extra["cum_H"][:, i_if]) - 1.0
    liq = float(strategy["risk"]["liq_ratio"])

    print(f"数据截至 {keep['t0']}，剩余 {len(keep['horizon'])} 个交易日，{sc.n_paths} 条路径")
    print(f"当前期货权益 {E0:,.2f}（期货盈利 {E0 - INITIAL_FUTURES:+,.0f}），1 手 IF 市值 {if_lot:,.0f}")
    print(f"沪深300 到 11-20 涨跌幅（模型）：5%/50%/95% 分位 = "
          f"{np.percentile(r_csi, 5):+.1%} / {np.percentile(r_csi, 50):+.1%} / {np.percentile(r_csi, 95):+.1%}")

    K = len(products)

    def vec(**lots):
        v = np.zeros(K, dtype=int)
        for p, n in lots.items():
            v[products.index(p)] = n
        return v

    plans = {
        "持有 3 手 IM": vec(IM=3),
        "减到 2 手 IM": vec(IM=2),
        "减到 1 手 IM": vec(IM=1),
        "全部平仓": vec(),
        "换成 1 手 IF": vec(IF=1),
    }
    crowd = {"人群不持仓": 0.0, "人群≈半手IF": 0.5 * if_lot, "人群≈1手IF": if_lot, "人群≈2手IF": 2 * if_lot}

    N = np.array(list(plans.values()))
    res = evaluate(sc, N, E0, liq)
    pnl = res.E_final - INITIAL_FUTURES

    print("\n=== 各方案的期货盈亏分布（到 11-20，模型估计）===")
    for j, name in enumerate(plans):
        q5, q50, q95 = np.percentile(pnl[:, j], [5, 50, 95])
        print(f"{name:<10} | 爆仓 {res.liquidated[:, j].mean():6.1%} | 期末盈亏<0 {np.mean(pnl[:, j] < 0):6.1%} | "
              f"盈亏 5%/50%/95% = {q5:+,.0f} / {q50:+,.0f} / {q95:+,.0f}")

    for band, (lo, hi) in BANDS.items():
        print(f"\n=== 晋级概率（晋级线基础值：期货盈利 {band}）===")
        header = f"{'方案':<10} | " + " | ".join(f"{c:>10}" for c in crowd)
        print(header)
        for j, name in enumerate(plans):
            cells = [f"{rel_prob(pnl[:, [j]], r_csi, nc, lo, hi)[0]:>10.1%}" for nc in crowd.values()]
            print(f"{name:<10} | " + " | ".join(cells))

    # ---- 止损线方案：期货盈利跌到 +3 万就全部平仓（模型只在每天收盘检查）----
    floor_pnl = 30_000.0
    floor = INITIAL_FUTURES + floor_pnl
    stop_plans = {
        "3手IM 不止损": (vec(IM=3), None),
        "3手IM +止损": (vec(IM=3), floor),
        "2手IM +止损": (vec(IM=2), floor),
        "1手IM +止损": (vec(IM=1), floor),
        "1手IF 不止损": (vec(IF=1), None),
        "1手IF +止损": (vec(IF=1), floor),
        "2手IF +止损": (vec(IF=2), floor),
        "全部平仓": (vec(), None),
    }
    print(f"\n=== 止损线方案（期货盈利跌破 {floor_pnl:+,.0f} 当天收盘全部平仓）===")
    print("晋级概率：基准 0–6 万；「平均」= 4 档人群假设的平均，「最差」= 其中最低；「保守平均」= 3–9 万口径的平均")
    print(f"{'方案':<10} | 爆仓 | 触发止损 | 晋级 平均 | 最差 | 保守平均 | 盈利≥20万 | 盈亏5% | 盈亏95%")
    for name, (n, fl) in stop_plans.items():
        r = evaluate(sc, n[None, :], E0, liq, floor=fl)
        p = r.E_final[:, [0]] - INITIAL_FUTURES
        base = [rel_prob(p, r_csi, nc, *BANDS["基准 0–6 万"])[0] for nc in crowd.values()]
        cons = [rel_prob(p, r_csi, nc, *BANDS["保守 3–9 万"])[0] for nc in crowd.values()]
        q5, q95 = np.percentile(p[:, 0], [5, 95])
        print(f"{name:<10} | {r.liquidated.mean():5.1%} | {r.stopped.mean():6.1%} | {np.mean(base):7.1%} | "
              f"{min(base):5.1%} | {np.mean(cons):6.1%} | {np.mean(p[:, 0] >= 200_000):7.1%} | "
              f"{q5:+,.0f} | {q95:+,.0f}")

    print("\n=== 模型在相对口径下的最优手数（基准 0–6 万）===")
    lo, hi = BANDS["基准 0–6 万"]
    for cname, nc in crowd.items():
        r = search(sc_screen, sc, kinds, E0, strategy, make_objective(nc, lo, hi, i_if),
                   log=lambda *_: None, label=cname)
        print(f"{cname:<10} | 最优 {lots_text(r.n, products):<20} | 晋级概率 {r.score:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
