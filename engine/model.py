"""情景模型：对数收益 → EWMA 标准化残差 → 块自助法路径 → 2612 价格 / 单手盈亏 / 单手保证金张量。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

MAD_TO_SD = 1.4826


def robust_sd(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    med = np.median(x)
    return float(MAD_TO_SD * np.median(np.abs(x - med)))


def build_returns(closes: dict[str, pd.Series], products: list[str], start: date,
                  bond_products: list[str], mad_k: float) -> tuple[pd.DataFrame, dict]:
    """8 个序列按日期取交集，算对数收益；国债主连 |r| > k × 稳健标准差 的日期整行剔除。"""
    cols = [p for p in products if p in closes]
    df = pd.DataFrame({p: closes[p] for p in cols})
    df = df[df.index >= pd.Timestamp(start)].dropna(how="any").sort_index()
    r = np.log(df).diff().dropna(how="any")
    removed = []
    mask = pd.Series(False, index=r.index)
    for p in cols:
        if p not in bond_products:
            continue
        sd = robust_sd(r[p].values)
        if sd <= 0:
            continue
        hit = r[p].abs() > mad_k * sd
        for d in r.index[hit]:
            removed.append({"date": d.date().isoformat(), "product": p,
                            "ret": round(float(r.at[d, p]), 6), "threshold": round(mad_k * sd, 6)})
        mask |= hit
    r = r[~mask]
    info = {
        "products": cols,
        "price_first": df.index[0].date().isoformat() if len(df) else None,
        "price_last": df.index[-1].date().isoformat() if len(df) else None,
        "n_prices": int(len(df)),
        "n_returns": int(len(r)),
        "removed_outliers": removed,
    }
    return r, info


def bond_roll_returns(closes: dict[str, pd.Series], bond_cols: list[str], mad_k: float) -> pd.Series:
    """现货债券部分的滚动收益：T 与 TF 主连对数收益的平均，换月跳空（离群点）记为 0。"""
    rets = []
    for p in bond_cols:
        if p not in closes:
            continue
        r = np.log(closes[p]).diff().dropna()
        sd = robust_sd(r.values)
        if sd > 0:
            r = r.where(r.abs() <= mad_k * sd, 0.0)
        rets.append(r.rename(p))
    if not rets:
        raise ValueError("T 与 TF 主连都缺失，无法滚动现货债券部分")
    return pd.concat(rets, axis=1).mean(axis=1, skipna=True).dropna()


def ewma_filter(r: np.ndarray, lam: float, init_n: int = 20) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """σ²_t = λσ²_{t-1} + (1-λ)r²_{t-1}。返回 (去均值的标准化残差 z, 下一日 σ, 历史 σ)。"""
    r = np.asarray(r, dtype=float)
    n, k = r.shape
    var = np.empty((n + 1, k))
    var[0] = np.mean(r[: min(init_n, n)] ** 2, axis=0)
    var[0] = np.where(var[0] > 0, var[0], 1e-8)
    for t in range(n):
        var[t + 1] = lam * var[t] + (1 - lam) * r[t] ** 2
    sig_hist = np.sqrt(var[:n])
    z = r / sig_hist
    z = z - z.mean(axis=0, keepdims=True)     # 零漂移：不预测涨跌
    return z, np.sqrt(var[n]), sig_hist


def block_bootstrap_indices(n_obs: int, n_paths: int, horizon: int, block_len: int,
                            rng: np.random.Generator) -> np.ndarray:
    block_len = max(1, min(block_len, n_obs))
    n_blocks = int(np.ceil(horizon / block_len))
    starts = rng.integers(0, n_obs - block_len + 1, size=(n_paths, n_blocks))
    idx = starts[:, :, None] + np.arange(block_len)[None, None, :]
    return idx.reshape(n_paths, -1)[:, :horizon]


def simulate_log_returns(z: np.ndarray, sigma0: np.ndarray, lam: float, n_paths: int, horizon: int,
                         block_len: int, rng: np.random.Generator) -> np.ndarray:
    """按日期整行抽取 z（块长 block_len），路径上的波动率按 EWMA 递推。返回 (路径, 天, 序列)。"""
    idx = block_bootstrap_indices(len(z), n_paths, horizon, block_len, rng)
    zz = z[idx]
    var = np.broadcast_to(np.asarray(sigma0, dtype=float) ** 2, (n_paths, z.shape[1])).copy()
    out = np.empty((n_paths, horizon, z.shape[1]))
    for h in range(horizon):
        rh = np.sqrt(var) * zz[:, h, :]
        out[:, h, :] = rh
        var = lam * var + (1 - lam) * rh ** 2
    return out


def equity_futures_path(S: np.ndarray, b0: float, D: np.ndarray, D0: int) -> np.ndarray:
    """F_h = S_h × (1 + b₀ × D_h ÷ D₀)：基差线性收敛，到期日 D=0 时 F=S。"""
    return S * (1.0 + b0 * np.asarray(D, dtype=float) / float(D0))


def count_trading_days_after(calendar: list[date], after: date, until: date) -> int:
    """交易日历中 (after, until] 的交易日数。"""
    return sum(1 for d in calendar if after < d <= until)


@dataclass
class Scenario:
    products: list[str]
    dates: list[date]
    F0: np.ndarray            # (K,)
    F: np.ndarray             # (P, H, K) 2612 价格路径
    pnl: np.ndarray           # (P, H, K) 单手累计盈亏
    margin: np.ndarray        # (P, H, K) 单手保证金
    spot: np.ndarray          # (P, H) 现货估值路径
    spot0: float
    fee_open: np.ndarray      # (K,) 单手开仓手续费
    fee_close: np.ndarray     # (K,) 单手平仓手续费
    margin0: np.ndarray       # (K,) 当前单手保证金
    tradable: np.ndarray      # (K,) bool
    seed: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def n_paths(self) -> int:
        return self.F.shape[0]


@dataclass
class ModelInputs:
    """建立情景所需的全部输入（与数据来源无关，单元测试可直接构造）。"""
    products: list[str]
    kinds: list[str]                  # equity / bond
    multipliers: np.ndarray
    margin_rates: np.ndarray
    fee_open: np.ndarray
    fee_close: np.ndarray
    z: np.ndarray                     # (N, K) 标准化残差
    sigma0: np.ndarray                # (K,) 下一日波动率
    S0: np.ndarray                    # (K,) t0 的指数 / 国债主连收盘
    F0: np.ndarray                    # (K,) t0 的 2612 收盘（缺失为 nan）
    D: np.ndarray                     # (H,) 每个路径日距股指 2612 到期的交易日数
    D0: int
    dates: list[date]
    etf0: float
    bond0: float
    etf_col: int                      # 沪深300 所在列
    bond_cols: list[int]              # T、TF 所在列（现货债券滚动）
    lam: float = 0.94
    block_len: int = 5

    @property
    def basis(self) -> np.ndarray:
        return self.F0 / self.S0 - 1.0


def build_scenario(mi: ModelInputs, n_paths: int, seed: int) -> Scenario:
    rng = np.random.default_rng(seed)
    H = len(mi.dates)
    r = simulate_log_returns(mi.z, mi.sigma0, mi.lam, n_paths, H, mi.block_len, rng)
    cum = np.cumsum(r, axis=1)
    K = len(mi.products)
    F = np.empty_like(cum)
    tradable = np.isfinite(mi.F0) & (mi.F0 > 0)
    F0 = np.where(tradable, mi.F0, mi.S0)
    for k in range(K):
        if mi.kinds[k] == "equity":
            S = mi.S0[k] * np.exp(cum[:, :, k])
            b0 = float(mi.basis[k]) if tradable[k] else 0.0
            F[:, :, k] = equity_futures_path(S, b0, mi.D[None, :], mi.D0)
        else:
            F[:, :, k] = F0[k] * np.exp(cum[:, :, k])
    pnl = (F - F0[None, None, :]) * mi.multipliers[None, None, :]
    margin = F * mi.multipliers[None, None, :] * mi.margin_rates[None, None, :]
    etf = mi.etf0 * np.exp(cum[:, :, mi.etf_col])
    bond_r = np.mean(r[:, :, mi.bond_cols], axis=2)
    bond = mi.bond0 * np.exp(np.cumsum(bond_r, axis=1))
    spot = etf + bond
    pnl[:, :, ~tradable] = 0.0
    margin[:, :, ~tradable] = 0.0
    return Scenario(
        products=list(mi.products), dates=list(mi.dates), F0=F0, F=F, pnl=pnl, margin=margin,
        spot=spot, spot0=mi.etf0 + mi.bond0, fee_open=np.where(tradable, mi.fee_open, 0.0),
        fee_close=np.where(tradable, mi.fee_close, 0.0),
        margin0=np.where(tradable, F0 * mi.multipliers * mi.margin_rates, 0.0),
        tradable=tradable, seed=seed,
    )
