# ADR-001：计算放在 GitHub Actions，结果用 GitHub Pages 静态网页展示

- Status: Accepted
- Date: 2026-10-08

## Context

用户要在手机上看到每天的「目标持仓 + 下单指令」。用户不是程序员，没有服务器，也不想维护服务器。
Claude 的运行环境访问不了新浪、东方财富、中金所，但 GitHub Actions 的服务器可以。网页需要在国内手机上稳定打开。

## Options Considered

1. 自建或租云服务器（Flask / FastAPI）定时计算并提供网页。
2. GitHub Actions 定时计算，生成静态 JSON，用 GitHub Pages 发布纯静态页面。
3. 只把结果发到聊天或邮件，不做网页。

## Decision

选方案 2：
- `recommend` 工作流负责计算并部署，`site/` 是纯 HTML/CSS/JS，读取 `data/latest.json`；
- 不引用外部 CDN，图表用手写 SVG；
- 引擎失败时，用缓存里上次成功的 `latest.json` 部署（`engine.fallback`）。

## Why

- 免费、不用运维，用户只需要在 GitHub 上点几下。
- Actions 能访问数据源，Claude 的沙箱不能。
- 纯静态页面加载快；不依赖外部 CDN，国内访问更稳。
- 有兜底机制，页面永远能打开，最多显示的是旧结果，并且会标明。

## Consequences

- 只有「计算时刻」的结果，不是实时更新的。
- GitHub 的定时任务可能延迟数小时（见 ADR-007）。
- 网页是公开仓库的 Pages，账户数据是公开的：用户已知情，账户是比赛模拟账户。
- Pages 要用户手动开启（Settings → Pages → Source 选 GitHub Actions）。
- 本地无法用真实数据验证，只能用合成数据和 Actions 日志。
