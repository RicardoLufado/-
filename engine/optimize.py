"""整数手数搜索：枚举初筛 → 前 30 名终评 → ±1 手坐标下降。"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .model import Scenario
from .portfolio import evaluate

Objective = Callable[[np.ndarray], np.ndarray]   # W (P, B) -> 分数 (B,)，越大越好


@dataclass
class SearchResult:
    n: np.ndarray
    score: float
    n_candidates: int
    n_feasible: int
    descent_steps: int
    top: list[dict] = field(default_factory=list)


def margin_ok(N: np.ndarray, margin0: np.ndarray, cap: float) -> np.ndarray:
    return np.abs(np.atleast_2d(N)) @ margin0 <= cap + 1e-6


def enumerate_candidates(sc: Scenario, kinds: list[str], E0: float, search: dict, margin_cap: float) -> np.ndarray:
    """最多两个股指品种（每个 −R..+R 手）× 最多一个国债品种（保证金上限内约 L 个等距手数）。"""
    K = len(kinds)
    cap = margin_cap * E0
    eq = [k for k in range(K) if kinds[k] == "equity" and sc.tradable[k]]
    bd = [k for k in range(K) if kinds[k] == "bond" and sc.tradable[k]]
    R = int(search["equity_lot_range"])
    lots = [x for x in range(-R, R + 1) if x != 0]

    eq_opts = [np.zeros(K, dtype=int)]
    for m in range(1, int(search["max_equity_products"]) + 1):
        for prods in itertools.combinations(eq, m):
            for combo in itertools.product(lots, repeat=m):
                v = np.zeros(K, dtype=int)
                v[list(prods)] = combo
                eq_opts.append(v)

    bd_opts = [np.zeros(K, dtype=int)]
    L = int(search["bond_levels"])
    for k in bd:
        mx = int(np.floor(cap / sc.margin0[k])) if sc.margin0[k] > 0 else 0
        if mx <= 0:
            continue
        levels = sorted({int(round(x)) for x in np.linspace(-mx, mx, L)} - {0})
        for x in levels:
            v = np.zeros(K, dtype=int)
            v[k] = x
            bd_opts.append(v)

    E = np.array(eq_opts)
    Bm = np.array(bd_opts)
    N = (E[:, None, :] + Bm[None, :, :]).reshape(-1, K)
    return N


def score(sc: Scenario, N: np.ndarray, E0: float, liq_ratio: float, objective: Objective) -> np.ndarray:
    W = evaluate(sc, N, E0, liq_ratio).W
    if getattr(objective, "needs_scenario", False):     # 目标函数需要知道是哪一套路径（如相对晋级线）
        return objective(W, sc)
    return objective(W)


def coordinate_descent(sc: Scenario, n0: np.ndarray, s0: float, E0: float, liq_ratio: float,
                       objective: Objective, cap: float, max_iters: int = 200) -> tuple[np.ndarray, float, int]:
    """每步在 8 个维度上各试 ±1 手（满足保证金约束的），取最好的；不再改进就停。"""
    n, best = n0.copy(), s0
    K = len(n)
    steps = 0
    for _ in range(max_iters):
        nbrs = []
        for k in range(K):
            if not sc.tradable[k]:
                continue
            for d in (-1, 1):
                v = n.copy()
                v[k] += d
                nbrs.append(v)
        nbrs = np.array(nbrs)
        nbrs = nbrs[margin_ok(nbrs, sc.margin0, cap)]
        if len(nbrs) == 0:
            break
        sc_n = score(sc, nbrs, E0, liq_ratio, objective)
        j = int(np.argmax(sc_n))
        if sc_n[j] > best + 1e-12:
            n, best = nbrs[j].copy(), float(sc_n[j])
            steps += 1
        else:
            break
    return n, best, steps


def search(sc_screen: Scenario, sc_final: Scenario, kinds: list[str], E0: float, strategy: dict,
           objective: Objective, log: Callable[[str], None] = print, label: str = "") -> SearchResult:
    risk, srch = strategy["risk"], strategy["search"]
    cap = float(risk["margin_cap"]) * E0
    liq = float(risk["liq_ratio"])

    N = enumerate_candidates(sc_screen, kinds, E0, srch, float(risk["margin_cap"]))
    ok = margin_ok(N, sc_screen.margin0, cap)
    Nf = N[ok]
    s1 = score(sc_screen, Nf, E0, liq, objective)
    top_k = min(int(srch["top_k"]), len(Nf))
    top_idx = np.argsort(-s1, kind="stable")[:top_k]
    Ntop = Nf[top_idx]
    s2 = score(sc_final, Ntop, E0, liq, objective)
    order = np.argsort(-s2, kind="stable")
    log(f"[{label}] 候选 {len(N)} 个，满足保证金约束 {len(Nf)} 个；初筛 {sc_screen.n_paths} 条路径，"
        f"前 {top_k} 名换 {sc_final.n_paths} 条路径复评")

    best_n, best_s, steps_total = Ntop[order[0]].copy(), float(s2[order[0]]), 0
    for j in order[: int(srch["descent_starts"])]:
        n, s, steps = coordinate_descent(sc_final, Ntop[j], float(s2[j]), E0, liq, objective, cap,
                                         int(srch["max_descent_iters"]))
        steps_total += steps
        if s > best_s + 1e-12:
            best_n, best_s = n, s
    log(f"[{label}] 坐标下降 {steps_total} 步，最优 {best_n.tolist()}，目标值 {best_s:.6g}")
    top = [{"n": Ntop[j].tolist(), "screen": float(s1[top_idx[j]]), "final": float(s2[j])} for j in order[:10]]
    return SearchResult(n=best_n, score=best_s, n_candidates=int(len(N)), n_feasible=int(len(Nf)),
                        descent_steps=steps_total, top=top)
