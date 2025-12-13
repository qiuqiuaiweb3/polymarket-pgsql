## 目标
从 Polymarket API 持续获取**新增/更新的 events（追踪所有 `updated_at` 变化）**，解析后存入 PGSQL 的“待观察”区；AI/规则挑选出可能存在**同一 event 内多 market 套利**的事件进入“持续观察”；对持续观察集合中的 markets 做**秒级**价格跟踪（paper trading 仅模拟成交，不真实下单），当满足套利条件时记录“模拟交易/收益”。

## 关键选择（已确认）
- **增量口径**：任何 `updated_at` 变化都要追踪（不是只看 `created_at`）。
- **套利类型**：同一 `event` 下多 `market`（典型 GMP：同一事件下多个互斥 outcome）。
- **执行方式**：只做 **paper trading**（不签名、不下真实订单）。

## 总体架构（发现→筛选→秒级盯盘→模拟执行）
- **发现层（Discovery / Gamma）**：增量拉取 events/markets 元数据（按 `updated_at`）→ 写入 staging。
- **筛选层（AI Filter）**：从 staging 取“候选 event”，产出 watchlist（event_id + market_ids）。
- **行情层（Price Stream / CLOB）**：对 watchlist market 做秒级价格更新（优先 WS）→ 写 `market_price_latest`（可选 `market_price_ticks`）。
- **策略层（Arb Engine）**：按 event 聚合 market 的可成交价格，判断是否存在确定性/近确定性套利。
- **执行层（Paper Trader）**：用 best bid/ask 规则模拟成交、计费、记录持仓与 PnL。

## 你应该使用哪些 API（高层清单）
### Gamma API（用于发现 & 元数据）
用途：找新增/更新的 events、解析 event-市场关系、状态变化、标签分类等。
- **Events 列表**：按 `updated_at` 倒序分页拉取最近变更 events（用于增量）。
- **Event by ID**：补全 event 详情（通常包含更完整字段/可能含嵌套 markets）。
- **Markets 列表 / Market by ID**：补拉/修复 market 元数据（状态、token ids、condition id 等）。
- （可选）**Tags / Series / Search**：用于“new 分类/主题/系列”召回和过滤。

### CLOB API（用于秒级价格流；paper trading 不下单也建议用）
用途：拿到“可成交”的价格（至少 best bid/ask），用于套利检测与模拟成交。
- **WebSocket 订阅**：对 watchlist market 订阅 ticker / best bid-ask / orderbook updates（推荐）。
- **Orderbook snapshot（REST）**：WS 重连时先拉快照，再接增量，保证一致性。
- （可选）**Trades**：用于更真实的滑点/成交模拟或特征工程。

> 备注：API 的具体 host/路径/参数以官方/你对接的文档为准；实现上按“支持分页 + 支持按更新时间排序/过滤”的通用模式设计即可。

## 增量同步策略（追踪 updated_at，保证不漏不重）
### Checkpoint
在 `sync_state` 表里持久化每个数据流的 checkpoint，例如：
- `gamma_events.last_updated_at`（时间戳）
- `gamma_events.last_seen_ids`（用于同一秒多条更新的并列去重，可选）

### 拉取策略（推荐：滑动窗口 + UPSERT）
- **每 30–120 秒**拉一次“按更新时间倒序”的 events 列表，取最近 N 页。
- 用“滑动窗口”避免乱序：查询时至少覆盖 `now - 10min` 的更新范围，或强制回拉最近 K 页。
- 对每条 event 做 **UPSERT**；若发现 event 更新，则触发一次 **Event by ID** 补全，并同步关联 markets。
- 对 markets 同样 **UPSERT**，并维护 `event_market_map`（或 markets 表里带 event_id）。

## 同一 event 多 market 套利（GMP）检测逻辑（paper trading）
假设某个 event 下有 N 个互斥 outcome market（最终**恰有一个**会 resolve 为 YES，其他为 NO）：
- **买入 YES 套利**：
  - 成本：\(\sum_i ask\_yes_i\)（用可成交的最优卖价/买入成本）
  - 结算收益：1（因为最终只有一个 YES 兑现 1，其余 0）
  - 条件：\(\sum_i ask\_yes_i + fees < 1\)
- **买入 NO 套利**（有时也成立，视产品结构/费用）：
  - 成本：\(\sum_i ask\_no_i\)
  - 结算收益：\(N-1\)（因为最终只有一个 market 为 YES，其余 \(N-1\) 都是 NO 兑现 1）
  - 条件：\(\sum_i ask\_no_i + fees < N-1\)

paper trading 建议：
- 买入用 **ask**，卖出用 **bid**（避免用 mid 造成虚假盈利）。
- 费用模型先做“可配置”：maker/taker、固定费率、或按成交额比例。
- 先实现“静态一口价成交”（best bid/ask），再升级到“按深度/滑点”。