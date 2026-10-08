"""打印 site/data/latest.json 的摘要，并检查有没有缺失项（Actions 的「结果摘要」步骤用）。

python scripts/summarize.py [路径]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CHECK_CONTRACT = ["F0", "S0", "last_close", "ref_price", "margin_per_lot", "vol_annual"]
CHECK_METRICS = ["promotion_prob", "liquidation_prob", "wipeout_prob", "W_p05", "W_p50", "W_p95",
                 "margin_ratio", "var99_1d"]


def find_missing(d: dict) -> list[str]:
    miss = []
    for k in ["generated_at", "data_asof", "status", "recommended_mode"]:
        if d.get(k) in (None, ""):
            miss.append(k)
    acc = d.get("account") or {}
    for k in ["total", "futures_equity"]:
        if acc.get(k) is None:
            miss.append(f"account.{k}")
    for k in ["value", "etf", "bond"]:
        if (acc.get("spot") or {}).get(k) is None:
            miss.append(f"account.spot.{k}")
    for c in d.get("contracts", []):
        for k in CHECK_CONTRACT:
            if c.get(k) is None:
                miss.append(f"contracts.{c.get('code')}.{k}")
        if c.get("kind") == "equity" and c.get("basis") is None:
            miss.append(f"contracts.{c.get('code')}.basis")
    for m in d.get("modes", []):
        for k in CHECK_METRICS:
            if (m.get("metrics") or {}).get(k) is None:
                miss.append(f"modes.{m.get('id')}.metrics.{k}")
        for o in m.get("orders", []):
            if o.get("ref_price") is None:
                miss.append(f"modes.{m.get('id')}.orders.{o.get('code')}.ref_price")
    if not d.get("modes"):
        miss.append("modes（空）")
    for s in d.get("data_sources", []):
        if s.get("status") != "ok":
            miss.append(f"数据源 {s.get('name')}：{s.get('status')}")
    return miss


def main(path: str = "site/data/latest.json") -> int:
    p = Path(path)
    if not p.exists():
        print("没有 latest.json")
        return 0
    d = json.loads(p.read_text(encoding="utf-8"))
    print(f"generated_at: {d.get('generated_at')} | data_asof: {d.get('data_asof')} | "
          f"市场: {d.get('market_status')} | 状态: {d.get('status')} | 推荐: {d.get('recommended_mode')}")
    if d.get("fallback"):
        print("!! 兜底结果：", d["fallback"])
    acc = d.get("account") or {}
    sp = acc.get("spot") or {}
    if acc:
        print(f"总金额 {acc.get('total'):,.2f} = 现货 {sp.get('value'):,.2f}（ETF {sp.get('etf'):,.2f} + 债 {sp.get('bond'):,.2f}）"
              f" + 期货 {acc.get('futures_equity'):,.2f}")
    h = d.get("horizon") or {}
    if h:
        print(f"路径：{h.get('start')} ~ {h.get('end')}，{h.get('trading_days')} 个交易日，D0={h.get('D0')}，日历：{h.get('calendar_source')}")
    print("数据源：")
    for s in d.get("data_sources", []):
        print(f"  {s['status']:>9} | {s['name']} | {s.get('rows')} 行 | {s.get('first_date')} ~ {s.get('last_date')} | {s.get('fetched_at')}")
    print("合约：")
    for c in d.get("contracts", []):
        basis = f"{c['basis'] * 100:+.2f}%" if c.get("basis") is not None else "—"
        vol = f"{c['vol_annual'] * 100:.1f}%" if c.get("vol_annual") is not None else "缺失"
        print(f"  {c['code']:>7} | t0收盘 {c.get('F0')} | 最新收盘 {c.get('last_close')}（{c.get('last_close_date')}）| "
              f"盘中 {c.get('spot_price')} {c.get('spot_time') or ''} | 参考价 {c.get('ref_price')} | 1手保证金 {c.get('margin_per_lot')} | "
              f"指数/主连 {c.get('S0')} | 基差 {basis} | 年化波动 {vol}")
    print("方案：")
    for m in d.get("modes", []):
        mt = m["metrics"]
        print(f"  {m['id']:>5} | {m['summary']} | 晋级 {mt['promotion_prob']:.2%} | 爆仓 {mt['liquidation_prob']:.2%} | "
              f"穿仓 {mt['wipeout_prob']:.2%} | W 5/50/95% {mt['W_p05']:,.0f} / {mt['W_p50']:,.0f} / {mt['W_p95']:,.0f} | "
              f"保证金 {mt['margin_ratio']:.1%} | 首日VaR99 {mt['var99_1d']:,.0f}")
        for o in m.get("orders", []):
            print(f"        下单：{o['text']}")
    rob = next((m.get("robustness") for m in d.get("modes", []) if m.get("id") == "A"), None)
    if rob:
        print(f"稳健性：{rob['min']:.2%} ~ {rob['max']:.2%}，三次最优相同：{rob['same_solution']}")
    print("说明：")
    for e in d.get("explanations", []):
        print("  -", e)
    for w in d.get("warnings", []):
        print("  [警告]", w)
    removed = (d.get("history_info") or {}).get("removed_outliers") or []
    print(f"剔除离群点 {len(removed)} 个：", ", ".join(f"{r['date']} {r['product']} {r['ret']:+.4f}" for r in removed))
    miss = find_missing(d)
    print("缺失项检查：" + ("无缺失 ✓" if not miss else f"{len(miss)} 项缺失"))
    for x in miss:
        print("  缺失：", x)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
