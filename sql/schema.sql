-- Polymarket → PGSQL (staging + watchlist + price stream + paper trading)
-- 目标：支持幂等 UPSERT、增量同步 checkpoint、秒级行情落库、以及 paper trading 回放。

-- ---------- Staging：原始元数据（保留 jsonb，方便后续字段演进） ----------
create table if not exists staging_events (
  event_id        bigint primary key,
  created_at      timestamptz,
  updated_at      timestamptz,
  status          text,
  slug            text,
  title           text,
  data            jsonb not null,
  ingested_at     timestamptz not null default now()
);

create index if not exists staging_events_updated_at_idx on staging_events (updated_at desc);
create index if not exists staging_events_ingested_at_idx on staging_events (ingested_at desc);

create table if not exists staging_markets (
  market_id       bigint primary key,
  event_id        bigint,
  created_at      timestamptz,
  updated_at      timestamptz,
  status          text,
  question        text,
  condition_id    text,
  clob_token_ids  jsonb,
  data            jsonb not null,
  ingested_at     timestamptz not null default now()
);

create index if not exists staging_markets_event_id_idx on staging_markets (event_id);
create index if not exists staging_markets_updated_at_idx on staging_markets (updated_at desc);

-- 如 event 与 market 不是一对多或需要保留“关系变更历史”，可启用映射表
create table if not exists staging_event_market_map (
  event_id    bigint not null,
  market_id   bigint not null,
  primary key (event_id, market_id)
);

-- ---------- 同步状态：checkpoint / watermarks ----------
create table if not exists sync_state (
  source        text primary key,          -- e.g. 'gamma_events', 'gamma_markets', 'clob_ws'
  checkpoint    jsonb not null,             -- e.g. {"last_updated_at":"2025-01-01T00:00:00Z"}
  updated_at    timestamptz not null default now()
);

-- ---------- Watchlist：持续观察集合（AI/规则输出） ----------
create table if not exists watch_events (
  event_id      bigint primary key,
  reason        text,
  score         double precision,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists watch_markets (
  market_id     bigint primary key,
  event_id      bigint not null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists watch_markets_event_id_idx on watch_markets (event_id);

-- ---------- 行情：最新价 + 可选 ticks ----------
-- 这里用“可成交价格”为主：buy 用 ask，sell 用 bid；也可以额外存 mid。
create table if not exists market_price_latest (
  market_id          bigint primary key,
  as_of              timestamptz not null,
  best_bid           numeric,   -- 以你统一的计价单位（如 0~1 概率价格，或美元）保存
  best_ask           numeric,
  mid                numeric,
  source             text not null, -- 'clob_ws' / 'clob_rest'
  raw                jsonb,
  updated_at         timestamptz not null default now()
);

create index if not exists market_price_latest_as_of_idx on market_price_latest (as_of desc);

create table if not exists market_price_ticks (
  market_id          bigint not null,
  as_of              timestamptz not null,
  best_bid           numeric,
  best_ask           numeric,
  mid                numeric,
  source             text not null,
  raw                jsonb,
  primary key (market_id, as_of)
);

create index if not exists market_price_ticks_as_of_idx on market_price_ticks (as_of desc);

-- ---------- 行情（按 outcome/asset 粒度）：用于精确回查 YES/NO ----------
-- CLOB WS market channel 是按 asset_id（token id）推送的；同一 market 里 YES/NO 是两个 asset。
create table if not exists asset_price_latest (
  asset_id          text primary key,
  market_id         bigint,
  outcome           text, -- 'YES' / 'NO'
  as_of             timestamptz not null,
  best_bid          numeric,
  best_ask          numeric,
  mid               numeric,
  source            text not null, -- 'clob_ws'
  raw               jsonb,
  updated_at        timestamptz not null default now()
);

create index if not exists asset_price_latest_market_id_as_of_idx on asset_price_latest (market_id, as_of desc);

create table if not exists asset_price_ticks (
  asset_id          text not null,
  as_of             timestamptz not null,
  market_id         bigint,
  outcome           text,
  best_bid          numeric,
  best_ask          numeric,
  mid               numeric,
  source            text not null,
  raw               jsonb,
  primary key (asset_id, as_of)
);

create index if not exists asset_price_ticks_as_of_idx on asset_price_ticks (as_of desc);

-- ---------- Paper trading：信号、模拟订单/成交、持仓、PnL ----------
create table if not exists arb_signals (
  signal_id      bigserial primary key,
  event_id       bigint not null,
  as_of          timestamptz not null,
  kind           text not null,        -- 'BUY_YES_ALL' / 'BUY_NO_ALL' / ...
  edge           numeric not null,     -- 理论边际（扣费前/后由你约定）
  detail         jsonb,                -- 记录参与 markets、用到的 bid/ask、阈值等
  created_at     timestamptz not null default now()
);

create index if not exists arb_signals_event_id_as_of_idx on arb_signals (event_id, as_of desc);

create table if not exists paper_orders (
  order_id       bigserial primary key,
  event_id       bigint not null,
  market_id      bigint not null,
  side           text not null,        -- 'BUY' / 'SELL'
  outcome        text not null,        -- 'YES' / 'NO'（按你的解析结果）
  qty            numeric not null,
  limit_price    numeric,              -- 可空：市价模拟
  status         text not null default 'NEW',
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  meta           jsonb
);

create index if not exists paper_orders_event_id_idx on paper_orders (event_id);
create index if not exists paper_orders_market_id_idx on paper_orders (market_id);

create table if not exists paper_fills (
  fill_id        bigserial primary key,
  order_id       bigint not null references paper_orders(order_id),
  market_id      bigint not null,
  filled_at      timestamptz not null,
  qty            numeric not null,
  price          numeric not null,
  fee            numeric not null default 0,
  meta           jsonb
);

create index if not exists paper_fills_market_id_filled_at_idx on paper_fills (market_id, filled_at desc);

create table if not exists paper_positions (
  market_id      bigint primary key,
  outcome        text not null,        -- 'YES' / 'NO'
  qty            numeric not null,
  avg_price      numeric not null,
  updated_at     timestamptz not null default now()
);

-- 可选：按 event 汇总的 PnL（回放/报表方便）
create table if not exists paper_pnl (
  event_id       bigint primary key,
  realized_pnl   numeric not null default 0,
  unrealized_pnl numeric not null default 0,
  updated_at     timestamptz not null default now()
);


