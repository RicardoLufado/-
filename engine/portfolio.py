"""组合评估：张量 × 手数向量 → 期货权益路径、爆仓 / 穿仓、期末总金额 W。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import Scenario

ELEMENT_BUDGET = 8_000_000   # 每批 (路径×天×候选) 的元素上限，控制内存


@dataclass
class EvalResult:
    W: np.ndarray          # (P, B) 期末总金额
    E_final: np.ndarray    # (P, B) 期末期货权益（爆仓后冻结，负值保留）
    liquidated: np.ndarray  # (P, B) bool 是否爆仓（含穿仓）
    wiped: np.ndarray      # (P, B) bool 是否穿仓（爆仓当天权益 < 0）
    stop_day: np.ndarray   # (P, B) 爆仓 / 止损日下标，都没发生为 -1
    dW1: np.ndarray        # (P, B) 第 1 天总金额变化（不含手续费）
    stopped: np.ndarray | None = None   # (P, B) bool 是否触发止损线（不含爆仓）


def evaluate(sc: Scenario, N: np.ndarray, E0: float, liq_ratio: float,
             floor: float | None = None) -> EvalResult:
    """N: (B, K) 整数手数（正 = 多，负 = 空）。

    E_h = E₀ − 开仓费 + Σ nᵢ × 单手累计盈亏ᵢ,h
    M_h = Σ |nᵢ| × 单手保证金ᵢ,h
    第一次出现 E_h < M_h × liq_ratio（或 E_h < 0）的那天按收盘价全部平仓，此后权益冻结；
    该日 E_h < 0 记为穿仓，负权益保留不截断。期末再扣平仓费。
    floor：止损线（期货权益）。收盘权益第一次低于它的那天全部平仓、此后冻结（模型只在收盘检查）。
    """
    N = np.atleast_2d(np.asarray(N, dtype=float))
    P, H, K = sc.pnl.shape
    B = N.shape[0]
    absN = np.abs(N)
    fee_open = absN @ sc.fee_open
    fee_close = absN @ sc.fee_close
    pnl2 = sc.pnl.reshape(P * H, K)
    mar2 = sc.margin.reshape(P * H, K)
    spotH = sc.spot[:, -1]
    spot1 = sc.spot[:, 0]

    W = np.empty((P, B))
    E_final = np.empty((P, B))
    liq = np.empty((P, B), dtype=bool)
    wiped = np.empty((P, B), dtype=bool)
    stop_day = np.empty((P, B), dtype=np.int32)
    stopped = np.zeros((P, B), dtype=bool)
    dW1 = np.empty((P, B))

    step = max(1, ELEMENT_BUDGET // (P * H))
    for s in range(0, B, step):
        e = min(B, s + step)
        Nb, Ab = N[s:e], absN[s:e]
        E = (E0 - fee_open[s:e])[None, None, :] + (pnl2 @ Nb.T).reshape(P, H, e - s)
        M = (mar2 @ Ab.T).reshape(P, H, e - s)
        breach_liq = (E < liq_ratio * M) | (E < 0)
        breach = breach_liq | (E < floor) if floor is not None else breach_liq
        any_b = breach.any(axis=1)
        first = breach.argmax(axis=1)
        E_stop = np.take_along_axis(E, first[:, None, :], axis=1)[:, 0, :]
        liq_first = np.take_along_axis(breach_liq, first[:, None, :], axis=1)[:, 0, :]
        Ef = np.where(any_b, E_stop, E[:, -1, :]) - fee_close[s:e][None, :]
        E_final[:, s:e] = Ef
        liq[:, s:e] = any_b & liq_first
        stopped[:, s:e] = any_b & ~liq_first
        wiped[:, s:e] = any_b & liq_first & (E_stop < 0)
        stop_day[:, s:e] = np.where(any_b, first, -1)
        W[:, s:e] = spotH[:, None] + Ef
        dW1[:, s:e] = (spot1 - sc.spot0)[:, None] + (E[:, 0, :] - (E0 - fee_open[s:e])[None, :])
    return EvalResult(W=W, E_final=E_final, liquidated=liq, wiped=wiped, stop_day=stop_day, dW1=dW1,
                      stopped=stopped)


def ramp_objective(W: np.ndarray, low: float, high: float) -> np.ndarray:
    """P(W ≥ τ)，τ ~ U[low, high] ⇔ mean(clip((W − low)/(high − low), 0, 1))。"""
    return np.clip((np.asarray(W) - low) / (high - low), 0.0, 1.0).mean(axis=0)


def neg_variance_objective(W: np.ndarray) -> np.ndarray:
    return -np.var(np.asarray(W), axis=0)


def metrics(sc: Scenario, n: np.ndarray, E0: float, liq_ratio: float, low: float | None = None,
            high: float | None = None, thr=None) -> dict:
    """单个组合的完整指标。thr：engine.threshold.Threshold；不给时按总金额区间 [low, high]。"""
    n = np.asarray(n, dtype=float)
    r = evaluate(sc, n[None, :], E0, liq_ratio)
    W = r.W[:, 0]
    margin_used = float(np.abs(n) @ sc.margin0)
    q5, q50, q95 = np.percentile(W, [5, 50, 95])
    if thr is not None:
        prom = float(thr.prob(W, sc)[0])
        above_low = float(thr.p_above(W, sc, thr.low)[0])
        above_high = float(thr.p_above(W, sc, thr.high)[0])
    else:
        prom = float(ramp_objective(W[:, None], low, high)[0])
        above_low, above_high = float(np.mean(W >= low)), float(np.mean(W >= high))
    pnl = r.E_final[:, 0] - E0
    return {
        "promotion_prob": prom,
        "p_above_low": above_low,
        "p_above_high": above_high,
        "futures_pnl_p05": float(np.percentile(pnl, 5)),
        "futures_pnl_p50": float(np.percentile(pnl, 50)),
        "futures_pnl_p95": float(np.percentile(pnl, 95)),
        "liquidation_prob": float(np.mean(r.liquidated[:, 0])),
        "wipeout_prob": float(np.mean(r.wiped[:, 0])),
        "W_p05": float(q5),
        "W_p50": float(q50),
        "W_p95": float(q95),
        "W_mean": float(np.mean(W)),
        "W_std": float(np.std(W)),
        "margin_used": margin_used,
        "margin_ratio": margin_used / E0 if E0 > 0 else None,
        "var99_1d": float(-np.percentile(r.dW1[:, 0], 1)),
        "fees": float(np.abs(n) @ (sc.fee_open + sc.fee_close)),
        "n_paths": int(sc.n_paths),
        "_W": W,
    }
