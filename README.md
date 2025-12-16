# polymarket-pgsql

把 Polymarket 的 **events/markets 元数据**与**秒级行情（paper trading）**落到你自己的 PGSQL，支持：
- `updated_at` 增量追踪（不漏不重）
- 同一 `event` 下多 `market` 套利机会检测（GMP）
- 仅 **paper trading**（不真实下单）

## 目录
- `docs/prd.md`：需求与架构说明
- `docs/polymarket数据结构.md`：Gamma 结构概念（event/market）
- `sql/schema.sql`：建议的 PG 表结构（staging + watchlist + 行情 + paper trading）
- `requirements.txt`：Python 依赖（建议在 venv 内安装）
- `env.example`：环境变量示例（复制为你本机的 `.env` 使用）
- `src/polymarket_pgsql/`：最小 Python 包（Gamma 客户端/配置）

## 最小落地步骤
1. **建表**
   - 在你的 PG 上执行 `sql/schema.sql`
2. **准备 Python 虚拟环境（推荐）**
   - 创建并安装依赖：
     - `./scripts/venv.sh`
   - 激活：
     - `source .venv/bin/activate`
   - 配置环境变量：
     - `cp env.example .env`（按需修改）
   - 如果你在 Debian/Ubuntu 上遇到 `ensurepip is not available`：
     - `sudo apt update && sudo apt install -y python3-venv`
   - 如果你遇到 `Permission denied`（脚本未设置可执行权限），任选其一：
     - `bash scripts/venv.sh`
     - `chmod +x scripts/venv.sh && ./scripts/venv.sh`
   - 如果你之前在缺少 `python3-venv` 时创建过 venv，可能会留下“残缺 `.venv`”（没有 `activate/pip`）：
     - `rm -rf .venv && bash scripts/venv.sh`
3. **连通性自检（Gamma）**
   - `PYTHONPATH=src python3 scripts/gamma_smoke_test.py`
4. **数据流（建议拆 3 个进程/任务）**
   - **Gamma Sync**：拉取 events/markets（按 `updated_at`）→ `staging_*`
   - **Watchlist Builder**：AI/规则选出 event/markets → `watch_*`
   - **Price Stream + Arb Engine + Paper Trader**：订阅 watch markets 的行情 → `market_price_latest` → 产出信号/模拟成交 → `arb_signals`/`paper_*`

## 研究脚本
- GMP（同一 event 多 outcome）Buy-YES 一揽子套利扫描：
  - `PYTHONPATH=src python3 scripts/find_gmp_arb_from_yes_prices_csv.py <csv_path> --fee-rate 0.002`

- CLOB WebSocket（Market Channel）实时订阅 + GMP 条件检测 + paper trading（按 `docs/今日目标.md`）：
  - 默认订阅 EVENT 45883 的 4 个 market（601697/601698/601699/601700），实时打印 YES/NO bid/ask、sum(YES ask)、是否满足 `sum(YES ask) < 1`、以及 paper trading PnL
  - 运行：
    - `PYTHONPATH=src python3 scripts/ws_gmp_arb_paper_trade.py --fee-rate 0.002`
  - 长时间运行并写入 PG（推荐跑 2-3 天后回查）：
    - 先在 PG 执行 `sql/schema.sql`（已包含 `asset_price_latest/asset_price_ticks`）
    - 然后运行（写 latest，默认每 5 秒批量写一次）：
      - `PYTHONPATH=src python3 scripts/ws_gmp_arb_paper_trade.py --fee-rate 0.002 --write-db`
    - 如需落 tick 明细（更占空间）：
      - `PYTHONPATH=src python3 scripts/ws_gmp_arb_paper_trade.py --fee-rate 0.002 --write-db --write-ticks --db-interval-s 2`
  - 备注：
    - WSS market channel 的 URL 是 `wss://ws-subscriptions-clob.polymarket.com/ws/market`（不要带末尾 `/`）
    - `CLOB_API_KEY / CLOB_API_SECRET / CLOB_API_PASSPHRASE` 可选（market channel 通常可匿名订阅）

> 备注：具体应使用的 Gamma/CLOB API 端点以你对接的官方文档为准；本仓库的设计按“分页 + 更新时间增量 + 幂等 UPSERT + WS 行情”的通用模式实现。
