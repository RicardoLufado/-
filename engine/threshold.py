"""晋级线：第 3000 名在 11-20 的位置（模型估计），以及「晋级概率」的计算。

两种口径（config/strategy.yaml 的 threshold.mode）：
- absolute：晋级线 = 总金额 τ ~ U[low, high]。晋级概率 = P(W ≥ τ)。
- relative：现货人人相同、涨跌互相抵消，排名只看期货账户。
    你的期货盈利   PnL = 期末期货权益 − 1,000,000
    第 3000 名的期货盈利 C = c0 + Nc × 沪深300 到期末的涨跌幅
      c0 ~ U[excess_low, excess_high]（榜单推算的基础值）
      Nc = 人群平均持有的多头市值（未知；对几档假设等权平均）
    晋级概率 = 平均_Nc P(PnL ≥ C)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .model import Scenario

INITIAL_FUTURES = 1_000_000.0


def ramp_prob(x: np.ndarray, low: float, high: float) -> np.ndarray:
    """P(x ≥ τ)，τ ~ U[low, high] ⇔ mean(clip((x − low)/(high − low), 0, 1))，按列（方案）平均。"""
    return np.clip((np.asarray(x) - low) / (high - low), 0.0, 1.0).mean(axis=0)


@dataclass
class Threshold:
    mode: str                                   # absolute / relative
    low: float                                  # absolute：总金额；relative：期货盈利
    high: float
    crowd_notional: list[float] = field(default_factory=lambda: [0.0])
    i_if: int | None = None                     # 沪深300 在情景里的列（relative 用）

    needs_scenario = True                       # 让 optimize.score 把情景一起传进来

    @property
    def relative(self) -> bool:
        return self.mode == "relative"

    def _excess(self, W: np.ndarray, sc: Scenario) -> tuple[np.ndarray, np.ndarray | None]:
        W = np.asarray(W, dtype=float)
        if W.ndim == 1:
            W = W[:, None]
        if not self.relative:
            return W, None
        pnl = W - sc.spot[:, -1][:, None] - INITIAL_FUTURES
        r = np.exp(sc.extra["cum_H"][:, self.i_if]) - 1.0
        return pnl, r[:, None]

    def prob(self, W: np.ndarray, sc: Scenario) -> np.ndarray:
        x, r = self._excess(W, sc)
        if r is None:
            return ramp_prob(x, self.low, self.high)
        return np.mean([ramp_prob(x - nc * r, self.low, self.high) for nc in self.crowd_notional], axis=0)

    __call__ = prob

    def p_above(self, W: np.ndarray, sc: Scenario, level: float) -> np.ndarray:
        x, r = self._excess(W, sc)
        if r is None:
            return (x >= level).mean(axis=0)
        return np.mean([((x - nc * r) >= level).mean(axis=0) for nc in self.crowd_notional], axis=0)

    def position(self, total_now: float, E0: float) -> float:
        """当前所在位置，和 low/high 同一单位。"""
        return E0 - INITIAL_FUTURES if self.relative else total_now

    def status(self, total_now: float, E0: float) -> str:
        x = self.position(total_now, E0)
        if x < self.low:
            return "追赶"
        if x < self.high:
            return "持平"
        return "锁定"


class VarianceObjective:
    """模式 D：absolute 口径最小化期末总金额方差；relative 口径现货互相抵消，只最小化期货权益方差。"""

    needs_scenario = True

    def __init__(self, relative: bool):
        self.relative = relative

    def __call__(self, W: np.ndarray, sc: Scenario) -> np.ndarray:
        W = np.asarray(W, dtype=float)
        if self.relative:
            W = W - sc.spot[:, -1][:, None]
        return -np.var(W, axis=0)


def from_config(cfg: dict, products: list[str], if_lot_value: float | None) -> Threshold:
    mode = cfg.get("mode", "absolute")
    if mode == "relative":
        lots = [float(x) for x in cfg.get("crowd_if_lots", [0])]
        return Threshold("relative", float(cfg["excess_low"]), float(cfg["excess_high"]),
                         [x * float(if_lot_value or 0.0) for x in lots], products.index("IF"))
    return Threshold("absolute", float(cfg["low"]), float(cfg["high"]))
