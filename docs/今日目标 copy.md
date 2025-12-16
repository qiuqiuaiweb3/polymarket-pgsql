# 今日目标

使用websocket获取EVENT ID 45883中各个market的 yes 和no的最新价格，直接计算是否满足开仓的套利条件，满足则paper trading计算pnl。
要实时打印时间、价格数据、是否满足套利条件、paper trading pnl结果。
然后将时间、价格数据、是否满足套利条件、paper trading pnl结果存入PGSQL中data test中，用于复盘。

目标event id：45883
目标market id:601697、601698、601699、601700

WebSocket
Used for all CLOB WSS endpoints, denoted {wss-channel}.
wss://ws-subscriptions-clob.polymarket.com/ws/

套利条件要求：所有yes总和小于1

data test 表结构：
    event_id bigint
    market_id bigint
    timestamp timestamptz
    yes_price numeric
    yes_size numeric
    no_price numeric
    no_size numeric
    is_arbitrage_eligible BOOLEAN DEFAULT FALSE
    pnl numeric












