# 架构决策记录（ADR）索引

规则：只增不删，旧决策的正文不改。新决策取代旧决策时，新建 ADR 并写 `Supersedes ADR-xxx`，
旧 ADR 只在 Status 行追加 `Superseded by ADR-yyy`。每份 ADR 至少包含
Context / Options Considered / Decision / Why / Consequences。

| 编号 | 标题 | 状态 |
|---|---|---|
| [ADR-001](ADR-001-static-site-on-github-actions-and-pages.md) | 计算放在 GitHub Actions，结果用 GitHub Pages 静态网页展示 | Accepted |
| [ADR-002](ADR-002-akshare-sina-data-with-cache-fallback.md) | AKShare（新浪）取数，股指历史用指数，每个接口有缓存回退 | Accepted |
| [ADR-003](ADR-003-scenario-model-ewma-block-bootstrap.md) | 情景模型：EWMA 过滤 + 块自助法，零漂移，基差线性收敛 | Accepted |
| [ADR-004](ADR-004-promotion-probability-objective-with-absolute-threshold.md) | 目标函数用晋级概率，晋级线用总金额区间 208–216 万 | 晋级线口径部分 Superseded by ADR-008 |
| [ADR-005](ADR-005-futures-only-no-options.md) | 只做 2612 期货，不做期权 | Accepted |
| [ADR-006](ADR-006-futures-equity-mark-to-market.md) | 期货权益按 2612 收盘价自动盯市估算 | Accepted |
| [ADR-007](ADR-007-schedule-shanghai-time-redundant-slots.md) | 定时按上海时间写，开盘前和晚上各 3 个时间点 | Accepted |
| [ADR-008](ADR-008-relative-threshold-stable-qualification.md) | 目标改为稳过前 3000 名，晋级线改用相对口径 | Accepted（Supersedes ADR-004 部分） |
| [ADR-009](ADR-009-leg-penalty-simplicity-rule.md) | 搜索加简洁规则，每多一个品种扣 0.3 个百分点 | Accepted |

新 ADR 模板：

```markdown
# ADR-NNN：标题

- Status: Accepted
- Date: YYYY-MM-DD
- Supersedes ADR-xxx（如有）

## Context
## Options Considered
## Decision
## Why
## Consequences
```
