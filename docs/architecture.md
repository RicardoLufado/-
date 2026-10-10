# 系统架构（当前有效）

> 只描述**当前**架构。为什么这样设计见 `docs/adr/`。

## 1. 总览

没有服务器。计算在 GitHub Actions 里跑，结果是一份静态 JSON，由 GitHub Pages 上的纯静态网页读取展示。

```
          定时（上海时间 08:25/08:45/09:05、20:05/21:05/22:05，周一至周五）
          手动 Run workflow · push main（state/engine/site/config 有改动）
                                │
                                ▼
┌──────────────────────── GitHub Actions: recommend ────────────────────────┐
│ actions/cache 恢复 data/cache/                                             │
│ python -m engine.run                                                       │
│   ├ engine.data      AKShare(新浪) 抓数，重试/超时/缓存回退 → MarketData   │
│   ├ engine.model     对数收益 → EWMA 残差 → 块自助法路径 → Scenario 张量  │
│   ├ engine.spot      现货估值（ETF + 债券）                                │
│   ├ engine.equity    期货权益按收盘价盯市                                  │
│   ├ engine.threshold 晋级线与晋级概率（relative / absolute）               │
│   ├ engine.optimize  整数手数搜索（模式 A / D）                            │
│   ├ engine.portfolio 组合评估（权益路径、爆仓、穿仓、止损）                │
│   ├ engine.orders    下单差额                                              │
│   └ engine.report    说明、局限、直方图                                    │
│ 失败时 → python -m engine.fallback（用上次成功的 latest.json）             │
│ python scripts/summarize.py（日志里打印关键数字与缺失项检查）              │
│ actions/cache 保存 data/cache/ → upload-pages-artifact(site/)              │
└────────────────────────────────────────────────────────────────────────────┘
                                │ deploy-pages
                                ▼
     https://ricardolufado.github.io/-/   site/index.html + app.js 读取 data/latest.json
```

## 2. 目录与模块职责

| 路径 | 职责 |
|---|---|
| `config/contracts.yaml` | 8 个 2612 合约的参数：乘数、最小变动、保证金率、手续费、历史序列代码、App 名称；每项注明来源 |
| `config/strategy.yaml` | 晋级线（`threshold`）、数据参数、模型参数、风险约束、搜索参数、站点参数 |
| `state/account.json` | 用户账户：期货权益（`updated_at` 收盘后）、持仓、现货市值、报名日期 |
| `state/leaderboard.json` | 历次周榜（前 100 名）与本人名次快照，用于校准晋级线 |
| `engine/config.py` | 读取以上配置；`Contract`（单手保证金、手续费、按最小变动取整）；北京时间工具 |
| `engine/data.py` | `Fetcher`：每个请求重试 3 次、超时 20 秒、间隔 1 秒，成功写缓存，失败读缓存；`fetch_market_data()` 拉交易日历、8 个历史序列、8 个 2612 日线和盘中价；回退日历 |
| `engine/model.py` | `build_returns()` 取日期交集、国债离群点剔除；`ewma_filter()`；`simulate_log_returns()` 块自助法；`build_scenario()` 生成 `Scenario`（价格、单手盈亏、单手保证金、现货路径、期末累计收益） |
| `engine/spot.py` | 现货估值：ETF 按沪深300 涨跌，债券按 T/TF 主连平均收益滚动 |
| `engine/equity.py` | `mark_to_market()`：期货权益 = 报告值 + 持仓按 2612 收盘价从 `updated_at` 盯市到 t0 |
| `engine/threshold.py` | `Threshold`：晋级线与晋级概率（relative：只比期货盈亏，人群 β 取平均；absolute：总金额区间）；`VarianceObjective`（模式 D） |
| `engine/portfolio.py` | `evaluate()`：手数矩阵 × 张量 → 期货权益路径，按天检查爆仓 / 穿仓 / 止损线；`metrics()` 汇总单个方案的指标 |
| `engine/optimize.py` | `enumerate_candidates()` → `score()`（含简洁规则扣分）→ `search()`：2000 条路径初筛，前 30 名换 10000 条复评，再做 ±1 手坐标下降 |
| `engine/orders.py` | `make_orders()`：目标持仓相对当前持仓的差额，先平后开 |
| `engine/report.py` | 自动生成的说明、局限、各方案的直方图 |
| `engine/run.py` | 主流程 `build()` 和输出 `write_outputs()`（latest.json + 历史存档）；命令行入口 |
| `engine/fallback.py` | 引擎失败时，用缓存里上次成功的 latest.json，并写明失败原因 |
| `engine/synthetic.py` | 合成行情（只用于测试和本地预览，输出带 `synthetic: true`） |
| `scripts/summarize.py` | 打印 latest.json 摘要，逐项检查有没有缺失 |
| `scripts/probe_sources.py` | 逐个探测数据接口 |
| `scripts/threshold_scenarios.py` | 用同一套路径对比各方案在不同晋级线、人群 β、止损假设下的晋级概率（只打印） |
| `site/` | `index.html` / `app.js` / `style.css`：手机优先的纯静态页面，手写 SVG，不用外部 CDN |
| `.github/workflows/` | `recommend`（计算 + 部署）、`tests`（pytest）、`probe`、`scenarios` |
| `tests/` | 全部用合成数据的 pytest，是系统行为的可执行规范 |

## 3. 数据流（一次 `engine.run`）

1. **读配置**：`contracts.yaml`、`strategy.yaml`、`account.json`。
2. **抓数**（`engine.data`）：
   - 交易日历：`tool_trade_date_hist_sina`；
   - 股指历史用**指数**：沪深300、上证50、中证500、中证1000（`stock_zh_index_daily`）；
   - 国债历史用新浪**主连**：TS0/TF0/T0/TL0（`futures_main_sina`）；
   - 2612 合约日线：`futures_zh_daily_sina`；
   - 盘中价：`futures_zh_spot`，只用于展示和参考价。
   - 失败的接口读缓存并写进 warnings；关键序列（沪深300、T/TF）缺失就让引擎报错，交给 fallback。
3. **收益与波动**：2023-05 以来 8 个序列取日期交集，算对数收益，剔除国债换月跳空，EWMA（λ=0.94）标准化并去均值。
4. **t0 定价**：t0 = 历史交集的最后一天；F0 = 2612 在 t0 的收盘价；基差 b0 = F0 / 指数 − 1。
5. **账户**：现货估值（`spot`）+ 期货权益盯市（`equity`）→ 当前总金额。
6. **情景**：路径覆盖 (t0, 11-20] 的每个交易日。
   - 股指 2612：F = 指数 × (1 + b0 × 剩余交易日 ÷ 当前剩余交易日)，即基差线性收敛；
   - 国债 2612：按主连收益滚动。
   - 生成单手盈亏、单手保证金、现货路径和期末累计收益张量。初筛 2000 条路径，终评 10000 条，固定种子。
7. **晋级线**（`threshold.from_config`）：
   - relative：你的期货盈利 ≥ c0 + Nc × 沪深300 涨跌，c0 ~ U[excess_low, excess_high]，对各档 Nc 取平均；
   - absolute：总金额 ≥ τ，τ ~ U[low, high]。
8. **搜索**：
   - 模式 A：晋级概率最大，减去简洁规则扣分；
   - 模式 D：波动最小（relative 口径下只看期货权益）；
   - 基准：空仓、1 手 TS。
   - 另外换 3 个随机种子重算模式 A，检验稳健性。
9. **输出**：`site/data/latest.json` 加上 `history/YYYYMMDD_HHMM.json`。缓存目录同时保存 `last_good/latest.json` 和历史存档，供下次运行和 fallback 使用。

## 4. 关键接口与数据契约

**`state/account.json`**（用户维护，测试只检查格式）

```json
{"updated_at": "YYYY-MM-DD", "futures_equity": 0.0,
 "positions": [{"code": "IF2612", "side": "long|short", "lots": 1, "avg_price": 0.0}],
 "spot_value": 0.0, "spot_value_asof": "YYYY-MM-DD",
 "registration_date": "YYYY-MM-DD", "spot_cost_date": "YYYY-MM-DD"}
```

- `futures_equity` 视为 `updated_at` 当天收盘后 App 显示的值。
- `positions` 只能写模型里有的 2612 合约，其他合约会被下单指令要求平仓。

**`site/data/latest.json`** 的顶层字段（`site/app.js` 和 `scripts/summarize.py` 依赖它，改结构必须三处一起改）：

| 字段 | 内容 |
|---|---|
| `generated_at`、`data_asof`、`market_status` | 计算时间、数据截至日期、交易日 / 休市 |
| `synthetic`、`fallback` | 是否合成数据、是否兜底结果 |
| `data_sources[]` | 每个数据源：status、rows、日期范围、抓取时间 |
| `account` | 总金额、现货拆分、期货权益（报告值 / 估算值 / 盯市明细）、持仓 |
| `threshold` | `mode`、`low`、`high`（与 mode 同单位）、`band_total_low/high`（按当前现货折算的总金额）、`position`、`crowd_if_lots`、`note` |
| `status`、`recommended_mode` | 追赶 / 持平 / 锁定；推荐的模式 |
| `horizon` | 剩余交易日、D0、日历来源 |
| `contracts[]` | 每个合约：t0 收盘、参考价（注明来源和时间）、单手保证金、基差、年化波动 |
| `modes[]` | A / D / empty / ts1，每个含 `lots`、`target_positions`、`orders`、`metrics`；A 另有 `robustness` |
| `histogram.modes{}` | 各方案自己的分箱和频率 |
| `explanations[]`、`limitations[]`、`warnings[]` | 说明、局限、警告 |
| `engine` | 版本、路径数、种子、运行时间、仓库名、运行链接 |

**模块间的主要接口**
- `build(spec, strategy, account, md, now, paths_screen, paths_final, robustness, keep)` → 结果 dict。
  `keep` 会带回 `sc_final`、`sc_screen`、`mi`、`E0` 等中间结果，供 `scripts/` 复用同一套路径。
- `evaluate(sc, N, E0, liq_ratio, floor=None)` → `EvalResult`（`W`、`E_final`、`liquidated`、`wiped`、`stopped`、`stop_day`、`dW1`）。
- 目标函数如果带 `needs_scenario = True`，`score()` 会以 `objective(W, sc)` 的形式调用它；`Threshold` 和 `VarianceObjective` 都是这样。

## 5. 依赖

- Python 3.11（CI），本地 3.13 也能跑。
- Python 包：akshare ≥ 1.19（实际版本写进 latest.json）、pandas、numpy、scipy、pyyaml、pytest。
- GitHub Actions：checkout@v4、setup-python@v5、cache/restore@v4、cache/save@v4、upload-pages-artifact@v3、deploy-pages@v4。
- 前端：不依赖任何外部 CDN，国内访问更稳。

## 6. 重要设计约束

- **不编造数据**：缺失就显示「数据缺失」；合成数据有红色横幅；兜底结果写明原因和展示的是哪次结果。
- **页面永远能打开**：引擎失败时用上次成功的结果部署（fallback）。
- **可复现**：随机种子固定，换 3 个种子检验结果是否稳健。
- **性能**：把「路径 × 天 × 合约」的张量预先算好，评估一个组合就是张量乘手数向量，几千个候选在几秒内评完。
  每批评估的元素数有上限（`ELEMENT_BUDGET`）。
- **风控简化**：只在每天收盘检查爆仓和止损；爆仓后权益冻结，负权益保留不截断。
- **测试离线**：全部用合成数据，不读真实账户数值。
- **定时不可靠**：GitHub 的定时可能延迟数小时甚至漏跑。所以用 6 个时间点做冗余；页面突出显示「计算时间」，超过 1 天标红。
