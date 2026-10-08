"""把模型结果整理成页面要用的说明、局限、直方图。所有数字都来自数据或模型计算。"""
from __future__ import annotations

import math

import numpy as np


def wan(x: float) -> str:
    return f"{x / 10000:,.1f} 万"


def pct(x: float | None, digits: int = 1) -> str:
    return "数据缺失" if x is None or not math.isfinite(x) else f"{x * 100:.{digits}f}%"


def classify_status(total: float, low: float, high: float) -> str:
    if total < low:
        return "追赶"
    if total < high:
        return "持平"
    return "锁定"


def histogram(W_by_mode: dict[str, np.ndarray], low: float, high: float, bins: int = 40) -> dict:
    """每个方案各用自己的区间（0.5%–99.5% 分位，并保证包含晋级线区间），超出部分并入首尾柱。"""
    modes = {}
    for k, w in W_by_mode.items():
        lo = min(float(np.percentile(w, 0.5)), low - 40000)
        hi = max(float(np.percentile(w, 99.5)), high + 40000)
        edges = np.linspace(lo, hi, bins + 1)
        c, _ = np.histogram(np.clip(w, lo, hi), bins=edges)
        modes[k] = {"edges": [round(float(e), 2) for e in edges], "freq": (c / len(w)).round(5).tolist()}
    return {"modes": modes, "note": "两端 0.5% 以外的值并入首尾两个柱"}


def positions_text(lots: dict[str, int], names: dict[str, str]) -> str:
    parts = [f"{names[p]} {'多' if n > 0 else '空'} {abs(n)} 手" for p, n in lots.items() if n != 0]
    return "、".join(parts) if parts else "空仓"


def explanations(ctx: dict) -> list[str]:
    """3–5 条由数据自动生成的说明。"""
    out = []
    total, low, high = ctx["total_now"], ctx["low"], ctx["high"]
    H = ctx["horizon_days"]
    if total < low:
        out.append(f"当前总金额 {wan(total)}（现货 {wan(ctx['spot_now'])} + 期货 {wan(ctx['E0'])}），"
                   f"距晋级线下沿 {wan(low)} 还差 {wan(low - total)}（需 +{(low / total - 1) * 100:.1f}%），"
                   f"距上沿 {wan(high)} 还差 {wan(high - total)}；剩余 {H} 个交易日。")
    else:
        out.append(f"当前总金额 {wan(total)}，已高于晋级线下沿 {wan(low)}"
                   f"{'，也高于上沿 ' + wan(high) if total >= high else '，距上沿 ' + wan(high) + ' 还差 ' + wan(high - total)}；"
                   f"剩余 {H} 个交易日。")

    eq = [c for c in ctx["contracts"] if c["kind"] == "equity" and c.get("basis") is not None]
    if eq:
        deepest = min(eq, key=lambda c: c["basis"])
        word = "贴水" if deepest["basis"] < 0 else "升水"
        others = "；".join(f"{c['product']} {c['basis'] * 100:+.2f}%" for c in eq if c is not deepest)
        out.append(
            f"{deepest['code']} 较{deepest['index_name']}{word} {abs(deepest['basis_points']):.1f} 点"
            f"（{deepest['basis'] * 100:+.2f}%），按剩余 {ctx['D0']} 个交易日折算年化{word} {abs(deepest['basis_annual']) * 100:.1f}%；"
            f"若指数不变，到 11-20 基差收敛给多头带来约 {deepest['carry_to_end']:+,.0f} 元/手（模型估计）。"
            + (f"其他：{others}。" if others else ""))

    vols = [c for c in ctx["contracts"] if c.get("vol_annual") is not None]
    if vols:
        out.append("当前年化波动率（EWMA，λ=0.94）：" +
                   "、".join(f"{c['product']} {c['vol_annual'] * 100:.1f}%" for c in vols) + "。")

    A = ctx["modes"].get("A")
    empty = ctx["modes"].get("empty")
    if A:
        rob = A.get("robustness") or {}
        rng = ""
        if rob.get("min") is not None:
            rng = f"（换 3 个随机种子重算最优解：{pct(rob['min'])}–{pct(rob['max'])}）"
        out.append(f"模式 A 推荐 {A['summary']}：晋级概率 {pct(A['metrics']['promotion_prob'])}{rng}，"
                   f"爆仓概率 {pct(A['metrics']['liquidation_prob'])}，穿仓概率 {pct(A['metrics']['wipeout_prob'])}"
                   + (f"；对比空仓 {pct(empty['metrics']['promotion_prob'])}。" if empty else "。"))
    status = ctx["status"]
    if status == "追赶":
        out.append("这是排名制锦标赛，只有前 3000 名晋级。Browne (1999)：目标在截止日前达到某水平时，"
                   "落后应加大风险、领先应锁定。每次运行都按当前权益和剩余天数重新优化，所以目前偏进攻。")
    elif status == "锁定":
        out.append("已高于晋级线区间上沿：按 Browne (1999)，领先时应降低风险锁定结果，模式 D 的方差最小。")
    else:
        out.append("处在晋级线区间内：模型在「提高达标概率」和「控制回撤」之间权衡。")
    if A and all(n == 0 for n in A["lots"].values()):
        out.append("提醒：晋级资格要求至少完成一笔有效交易（委托并成交），空仓方案需另做 1 手 TS 等小额交易。")
    return out[:6]


def limitations(ctx: dict) -> list[str]:
    removed = ctx["removed_outliers"]
    rm_txt = "、".join(f"{r['date']} {r['product']}（{r['ret'] * 100:+.2f}%）" for r in removed[:12])
    if len(removed) > 12:
        rm_txt += f" 等共 {len(removed)} 个"
    return [
        "新浪国债主力连续合约没有复权，季度换月有小跳空：日收益绝对值超过 6 倍稳健标准差（MAD）的点已整行剔除"
        + (f"：{rm_txt}。" if removed else "（本次没有点被剔除）。") + "剩下的小跳空仍会混在历史收益里。",
        "股指历史用的是指数而不是主力连续合约（避免换月跳空）；2612 价格按「基差线性收敛到到期日」近似。",
        "现货 ETF 部分按沪深300指数涨跌近似，忽略 ETF 跟踪误差和分红；债券部分（22附息国债10，"
        "2022-05 发行的 10 年期，剩余期限约 5.6 年，待核实）用 T 与 TF 主连日收益平均值近似。",
        "零漂移：模型不预测涨跌，只用 EWMA 波动率 + 块自助法重排历史冲击，不含历史上没出现过的极端情景。",
        "每天只在收盘检查一次爆仓：盘中可能更早被强平。爆仓后本模型把权益冻结（简化），真实规则下仍可继续交易。",
        "晋级线区间 208–216 万是用两期前 100 名榜单粗估的量级参考，不是官方数字。",
        "手续费按中金所标准估算（待核实）；期权暂未纳入，只做 2612 期货。",
    ]
