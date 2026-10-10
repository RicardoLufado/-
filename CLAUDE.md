# CLAUDE.md — Agent 长期工作规则

本文件只放**长期有效**的工作规则。当前任务和进度写在 `TASK.md`，系统结构写在
`docs/architecture.md`，重要决策写在 `docs/adr/`。只有长期规则变化时才修改本文件。

## 1. 每次开始新会话（按顺序）

1. 读本文件 `CLAUDE.md`
2. 读 `TASK.md`（当前目标、进行中、下一步、待确认问题）
3. 读 `docs/architecture.md`
4. 读与本次任务相关的 ADR（`docs/adr/README.md` 有索引）
5. `git status`，`git log --oneline -15`
6. 看与任务相关的 `tests/`
7. 如需线上最新状态：看 GitHub Actions 最近一次 `recommend` 运行的「结果摘要」步骤日志
   （`scripts/summarize.py` 的输出：数据日期、各合约价格、各方案指标、缺失项检查）

恢复状态之后再动手；与 `TASK.md` 不一致的地方，以代码和 git 历史为准，并顺手修正 `TASK.md`。

## 2. 与用户协作

- 全程用**中文**。用户是金融专业学生，不是程序员：需要用户在 GitHub / App 上操作的事，写成一步一步可照做的说明。
- **不许编造数据**。页面上的每个数字必须来自：带时间戳的数据抓取、`state/*.json`，或模型计算（标「模型估计」）。
  拿不到就显示「数据缺失」。不输出「保证盈利」之类的话。
- **下单永远由用户在 App 里手动完成**。不自动登录、不操作任何账户；仓库、日志、前端代码里不得出现任何令牌。
- 会改变推荐方向的模型或策略改动（目标函数、晋级线口径、风险约束），先向用户解释选项和影响，由用户决定。
  路线图里的新功能阶段，先给计划，用户确认后再动手。
- 用户发来 App 截图或录屏时：读出数字后更新 `state/account.json` 或 `state/leaderboard.json`，
  并说明数字来源（哪张截图、哪天收盘后）。

## 3. 运行环境事实（长期有效）

- 本地沙箱**访问不了**新浪、东方财富、中金所和 `github.io`。真实数据只在 GitHub Actions 里跑。
  本地验证用 `python -m engine.run --synthetic` 或测试里的假 akshare 模块。
- 读 Actions 日志、触发工作流用 GitHub MCP 工具（`get_job_logs`、`actions_run_trigger`）。
  可手动触发的工作流：`recommend`（重算并部署网页）、`scenarios`（方案对比，只打印结果）、`probe`（数据源探测）。
- push 到 `main` 且改动了 `state/**`、`engine/**`、`site/**`、`config/**` 时，会自动触发 `recommend`：
  重算并**覆盖线上网页**。推送前要确认这是你想要的。
- CI 用 **Python 3.11**：不要用 3.12+ 才有的语法，比如同类引号嵌套的 f-string。

## 4. 修改代码前

- 先读相关模块和测试。确认改动会不会影响 `site/data/latest.json` 的结构：
  `site/app.js` 和 `scripts/summarize.py` 都依赖它，改结构必须三处一起改。
- 动到模型、目标函数、晋级线口径、风险约束或数据源时，判断是否需要新 ADR（见第 7 节）。

## 5. 修改代码后（检查流程）

1. `python -m pytest -q` 全部通过。条件允许时也用 Python 3.11 跑一遍。
2. 改了引擎：`python -m engine.run --synthetic --out <临时目录>` 能跑通，
   再用 `python scripts/summarize.py <临时目录>/latest.json` 看缺失项。
3. 改了页面：`node -e "new Function(require('fs').readFileSync('site/app.js','utf8'))"` 检查语法；
   用合成数据在 390px 宽度下截图，确认没有横向滚动、没有报错。
4. `git diff` 自查一遍：有没有误改、有没有生成物混进来。
5. 推送后在 Actions 确认：`recommend` 成功，「结果摘要」显示「缺失项检查：无缺失」。

## 6. 测试要求

- 测试是系统行为的**可执行规范**。新功能、修 bug 都要同时补测试。
- 测试全部用合成数据，**不访问网络**。
- 测试**不得依赖** `state/account.json` 的具体数值，统一用 `tests/helpers.base_account()`；
  对真实 `state/*.json` 只做格式检查（`tests/test_state_files.py`）。
- `tests/test_docs.py` 检查项目记忆文件的结构（TASK.md 的标题、ADR 的命名和必需章节、
  architecture.md 是否覆盖所有 engine 模块）。新增 engine 模块时要同步写进 `docs/architecture.md`。

## 7. Git 规范

- 小而清晰的 commit。message 用中文：第一行写**改了什么**，正文写**为什么改**。
- **不改写已推送的历史**：不 rebase、不 amend、不 force-push 已推送的提交。新的变化就新建 commit。
- 用户授权直接推送 `main`。如果平台另外指定了工作分支，同一批提交也同步推送到那个分支。
- 提交前确认测试通过。命令里用 `set -o pipefail`，防止 `pytest | tail` 这种管道把失败吞掉。
- 生成物不进仓库：`data/cache/`、`site/data/latest.json`、`site/data/history/`（已写进 `.gitignore`）。

## 8. 文档维护原则

| 文件 | 写什么 | 什么时候改 |
|---|---|---|
| `TASK.md` | 只写**当前状态**：目标、约束、已完成、进行中、下一步、待确认问题；不写流水账（历史看 git log） | 每个重要 milestone 之后 |
| `docs/architecture.md` | 只写**当前有效**的架构：模块、数据流、接口、依赖、约束 | 架构变化时 |
| `docs/adr/ADR-NNN-*.md` | 重大技术或策略决策：Context / Options Considered / Decision / Why / Consequences | 产生新的重大决策时新建 |
| `README.md` | 面向用户的中文操作指南 | 用户可见的流程变化时 |
| `CLAUDE.md` | 本文件：长期工作规则 | 只在长期规则变化时 |

- ADR 只增不删，旧决策的正文不改。新决策取代旧决策时：
  - 新建 ADR，写明 `Supersedes ADR-xxx`；
  - 旧 ADR 只在 Status 行追加 `Superseded by ADR-yyy`；
  - 同步更新 `docs/adr/README.md` 的索引。
- 每完成一个重要 milestone，依次：更新 `TASK.md` → 架构变了就更新 `architecture.md` → 有新决策就新增 ADR →
  更新或新增测试 → 跑测试 → 检查 `git diff` → 提交 commit。
