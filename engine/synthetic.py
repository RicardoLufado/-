"""合成数据：只用于单元测试和本地预览。输出会被标记为 synthetic，页面会显示红色横幅。"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .config import ContractSpec, now_beijing
from .data import MarketData, SourceRecord, weekday_calendar

# 合成参数（不是真实数据）：年化波动率、期末水平、2612 相对基差
SYN = {
    "IF": (0.18, 4250.0, -0.004),
    "IH": (0.16, 2770.0, -0.002),
    "IC": (0.22, 7150.0, -0.012),
    "IM": (0.26, 7080.0, -0.018),
    "TS": (0.006, 102.6, 0.0),
    "TF": (0.016, 106.4, 0.0),
    "T": (0.025, 109.4, 0.0),
    "TL": (0.06, 116.9, 0.0),
}


def synthetic_market_data(spec: ContractSpec, end: date = date(2026, 9, 30), seed: int = 7,
                          start: date = date(2023, 5, 4)) -> MarketData:
    rng = np.random.default_rng(seed)
    calendar = weekday_calendar(date(2023, 1, 2), date(2026, 12, 31))
    days = [d for d in calendar if start <= d <= end]
    n = len(days)
    K = len(spec.contracts)
    corr = np.full((K, K), 0.0)
    eq = [i for i, c in enumerate(spec.contracts) if c.kind == "equity"]
    bd = [i for i, c in enumerate(spec.contracts) if c.kind == "bond"]
    for i in eq:
        for j in eq:
            corr[i, j] = 0.85
    for i in bd:
        for j in bd:
            corr[i, j] = 0.8
    for i in eq:
        for j in bd:
            corr[i, j] = corr[j, i] = -0.2
    np.fill_diagonal(corr, 1.0)
    L = np.linalg.cholesky(corr)
    vols = np.array([SYN[c.product][0] for c in spec.contracts]) / np.sqrt(252)
    shocks = rng.standard_t(df=5, size=(n, K)) / np.sqrt(5 / 3)
    r = (shocks @ L.T) * vols
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    closes, daily = {}, {}
    for k, c in enumerate(spec.contracts):
        vol, level, basis = SYN[c.product]
        path = level * np.exp(np.cumsum(r[:, k]) - np.sum(r[:, k]))
        closes[c.product] = pd.Series(path, index=idx)
        recent = idx[-60:]
        fut = path[-60:] * (1 + basis)
        daily[c.product] = pd.DataFrame({"date": recent, "close": fut, "settle": fut})
    ts = now_beijing().isoformat(timespec="seconds")
    sources = [SourceRecord(key="synthetic", name="合成数据（仅测试）", call="engine.synthetic",
                            status="synthetic", fetched_at=ts, rows=n,
                            first_date=days[0].isoformat(), last_date=days[-1].isoformat())]
    return MarketData(calendar=calendar, calendar_source="合成：工作日 − 已知节假日", closes=closes,
                      contract_daily=daily, spot_quotes={}, sources=sources,
                      warnings=["这是合成数据，只用于测试页面和代码，数字没有任何市场含义"],
                      synthetic=True, akshare_version=None)
