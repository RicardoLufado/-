# 交易库：中金所杯「一键持仓建议」

2026「中金所杯」模拟交易赛道用的持仓建议网站。周一至周五按上海时间自动抓数据、跑模型，
把「目标持仓 + 下单指令」发布到 GitHub Pages，手机打开就能看：

- 开盘前：**08:25、08:45、09:05**；晚上：**20:05、21:05、22:05**（新浪的指数日线要到晚上才更新当天收盘）。
- GitHub 的定时任务可能延迟几十分钟甚至漏跑，所以设了备用时间点；跑到哪次都行，最新一次覆盖之前的结果。

- 网址：<https://ricardolufado.github.io/-/>（第一次需要按下面「首次设置」启用）
- 下单永远由你在东方财富期货 App 里手动完成；本仓库不会、也不能登录任何账户。
- 仅用于模拟比赛，不构成真实投资建议。

## 首次设置（只做一次）

1. 打开仓库页面 → 顶部 **Settings** → 左侧 **Pages** → **Build and deployment** 下的 **Source** 选 **GitHub Actions**。
2. 点顶部 **Actions**。如果出现绿色按钮「I understand my workflows, go ahead and enable them」，点它启用。
3. 在 Actions 左侧点 **recommend** → 右侧 **Run workflow** → 再点绿色 **Run workflow**。
4. 等 3–5 分钟，运行变成绿色对勾后，打开上面的网址。

## 每天怎么用

- 开盘前（09:05 之后）打开网址，看「推荐持仓」和「下单指令」；先看顶部的计算时间是不是今天早上。
- 想立刻重算：页面上点「去 GitHub 重新计算」→ **Run workflow** → **Run workflow**，几分钟后回页面点「重新加载」。
- **成交后更新持仓**：在 GitHub 打开 `state/account.json` → 右上角铅笔图标 ✏️ → 修改 → **Commit changes**。
  提交后会自动重算。格式：

```json
{
  "updated_at": "2026-10-09",
  "futures_equity": 1000000.00,
  "positions": [
    {"code": "IM2612", "side": "long", "lots": 2, "avg_price": 7022.2}
  ],
  "spot_value": 984052.96,
  "spot_value_asof": "2026-09-30",
  "registration_date": "2026-09-20",
  "spot_cost_date": "2026-09-18"
}
```

`side` 只能是 `long`（多）或 `short`（空）。

- **建议收盘后（15:15 以后）更新**：`futures_equity` 填 App 里的期货权益，`updated_at` 填当天日期。
- 之后不用每天改：每次运行会按 2612 最新收盘价把持仓自动盯市，估算当前期货权益（页面标「模型估计」）。
  估算 = 你填的权益 + Σ 方向 × 手数 × (最新收盘 − updated_at 当天收盘) × 乘数。有空时用 App 真实权益校准即可。
- 买卖之后必须更新 `positions`，否则下单指令会按旧持仓算差额。
`spot_value` / `spot_value_asof` 是 App 显示的现货市值和日期，可以每周更新一次，模型会从那天起自己滚动估值。

## 出问题时

- 页面顶部会用红字写明：数据过期（超过 1 天）、某个数据源抓取失败用了缓存、或本次计算失败显示的是上次结果。
- 看日志：**Actions** → 点最近一次 **recommend** 运行 → **build** → 展开「运行引擎」和「结果摘要」。
  把红色报错那几行复制下来发给 Claude 即可。
- 只想测数据源通不通：**Actions** → **probe** → **Run workflow**，日志里会逐个列出接口是否成功、耗时和前 3 行。

## 模型一句话

股指用指数、国债用新浪主连，2023-05 以来的对数收益 → EWMA（λ=0.94）标准化、去均值（不预测涨跌）→
块长 5 的块自助法生成到 11-20 的 1 万条路径 → 股指 2612 基差线性收敛、国债 2612 按主连滚动 →
逐日检查爆仓 / 穿仓 → 期末总金额 W = 现货 + 期货权益。

- **模式 A（推荐）**：在「开仓保证金 ≤ 70% 权益」约束下，搜索整数手数，最大化 P(W ≥ τ)，τ ~ U[208 万, 216 万]。
- **模式 D（锁定）**：同样的搜索，最小化 W 的方差。
- 基准：空仓、1 手 TS。每个方案都给出晋级概率、爆仓 / 穿仓概率、W 的 5%/50%/95% 分位、保证金占用率、首日 99% VaR。

参数都在 `config/strategy.yaml`（路径数、70% 上限、τ 区间、liq_ratio、随机种子）和 `config/contracts.yaml`（合约参数及来源）。

## 目录

```
config/        合约参数、策略参数
state/         account.json（你的账户）
engine/        数据抓取、情景模型、优化、输出
site/          网页（纯 HTML/CSS/JS，无外部 CDN）；site/data/ 由 Actions 生成
scripts/       probe_sources.py 数据源探测
tests/         pytest（全部用合成数据）
.github/workflows/  recommend（计算+部署）、probe（探测）、tests（单元测试）
```

## 本地运行（可选）

```bash
pip install -r requirements.txt
python -m pytest -q                 # 单元测试
python -m engine.run --synthetic    # 合成数据（页面会显示红色「仅测试」横幅）
python -m engine.run                # 真实数据（需要能访问新浪财经）
python -m http.server -d site 8000  # 浏览器打开 http://localhost:8000
```
