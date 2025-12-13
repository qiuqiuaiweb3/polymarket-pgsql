# polymarket-pgsql

把 Polymarket 的 **events/markets 元数据**与**秒级行情（paper trading）**落到你自己的 PGSQL，支持：
- `updated_at` 增量追踪（不漏不重）
- 同一 `event` 下多 `market` 套利机会检测（GMP）
- 仅 **paper trading**（不真实下单）

## 目录
- `docs/prd.md`：需求与架构说明
- `docs/polymarket数据结构.md`：Gamma 结构概念（event/market）
- `sql/schema.sql`：建议的 PG 表结构（staging + watchlist + 行情 + paper trading）

## 最小落地步骤
1. **建表**
   - 在你的 PG 上执行 `sql/schema.sql`
2. **数据流（建议拆 3 个进程/任务）**
   - **Gamma Sync**：拉取 events/markets（按 `updated_at`）→ `staging_*`
   - **Watchlist Builder**：AI/规则选出 event/markets → `watch_*`
   - **Price Stream + Arb Engine + Paper Trader**：订阅 watch markets 的行情 → `market_price_latest` → 产出信号/模拟成交 → `arb_signals`/`paper_*`

> 备注：具体应使用的 Gamma/CLOB API 端点以你对接的官方文档为准；本仓库的设计按“分页 + 更新时间增量 + 幂等 UPSERT + WS 行情”的通用模式实现。
