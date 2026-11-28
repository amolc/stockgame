# StockGame — 50-Account Optimized Architecture & Flow Guide

This document defines the **target production architecture** for scaling StockGame to **50+ accounts (~300+ bots)** with sub-second reaction time, parallel per-account execution, and zero Binance IP rate-limit bans (`-1003`).

---

## 1. Current vs. Target 50-Account Architecture

### Current Architecture (2 Processes, In-Memory WS)
- `stockgame` (web) and `stockgame-bot` (60s polling loop) run as separate PM2 processes, each holding its own in-memory WebSocket cache.
- `run_all_active_bots()` iterates sequentially over bots, which becomes a bottleneck at 50 accounts (300+ bots).

### Target Architecture (`Market Data Hub + Redis + Celery Workers`)

```mermaid
flowchart TB
    subgraph Binance["Binance Exchange (Per-IP Rate Limit: 2400 weight/min)"]
        WS_M["Market WebSocket (miniTicker + kline)"]
        WS_U["50x User Data WebSockets (ACCOUNT_UPDATE)"]
        REST["REST API (Initial Seed + Order Execution)"]
    end

    subgraph Hub["Process 1: Market Data Hub (ws_hub)"]
        MPOOL["Single Market WS Pool (1 per env/market)"]
        UPOOL["User Stream Pool (Balances & Positions)"]
        EVT{"Candle Closed? (k.x == true)"}
    end

    subgraph RedisLayer["Redis (Shared Cache + Celery Broker + PubSub)"]
        RCACHE["Live Prices, 1500-Candle OHLCV & Account Balances"]
        RQUEUE["Celery Task Queue (run_account_symbol)"]
        RPUB["Pub/Sub Channel (UI Updates)"]
    end

    subgraph Celery["Process 2: Celery Worker Pool (4-8 Concurrent Workers)"]
        STRAT["StrategyService (Fast/Slow EMA Crossover)"]
        PRE["Pre-Flight Validator (LOT_SIZE + MIN_NOTIONAL + Cash)"]
        EXEC["ExecutionService (Paper / Demo / Live)"]
    end

    subgraph Storage["Persistence"]
        DB[("PostgreSQL (db.quantbots.co: BotConfig, Trade, SignalLog)")]
    end

    subgraph Web["Process 3: Django Web Server (stockgame)"]
        API["Web Views & SSE Broadcaster"]
        UI["Super Admin Browser UI"]
    end

    REST -->|"1. Bootstrap 1500 candles + exchangeInfo"| MPOOL
    WS_M -->|"2. Live price & candle ticks"| MPOOL
    WS_U -->|"2b. Live balances & positions"| UPOOL

    MPOOL -->|"Write ticks & candles"| RCACHE
    UPOOL -->|"Write account snapshots"| RCACHE
    MPOOL -->|"Publish price/balance delta"| RPUB
    MPOOL --> EVT
    EVT -->|"k.x == true (Enqueue 1 task per active account)"| RQUEUE

    RQUEUE -->|"Fan out 50 accounts in parallel"| STRAT
    RCACHE -->|"Read candles (0 Binance REST calls)"| STRAT
    STRAT -->|"BUY / SELL / HOLD"| PRE
    PRE -->|"Valid order"| EXEC
    PRE -->|"Below min notional / insufficient cash"| DB
    EXEC -->|"Place market order (if Demo/Live)"| REST
    EXEC -->|"Atomic Trade + SignalLog + Cash update"| DB
    EXEC -->|"Publish trade/signal event"| RPUB

    RCACHE -->|"Read live prices, charts & balances"| API
    RPUB -->|"SSE Push (0 idle polls)"| UI
    API --> UI
```

---

## 2. Event-Driven Execution Flow (50 Accounts in Parallel)

When Binance emits a closed candle (`k.x == true`), the Market Hub writes the finalized candle to Redis **once** and dispatches Celery tasks for all active accounts on that `(symbol, interval)` to execute concurrently:

```mermaid
sequenceDiagram
    autonumber
    participant B as Binance WS
    participant Hub as Market Hub (ws_hub)
    participant R as Redis (Cache & Broker)
    participant W1 as Celery Worker 1 (nitin)
    participant W2 as Celery Worker 2 (amol)
    participant Ex as Binance Order REST
    participant DB as PostgreSQL
    participant UI as Browser UI (SSE)

    B->>Hub: kline event (BTCUSDT 15m, k.x = false)
    Hub->>R: Update active candle & live price
    R-->>UI: Push live price tick via SSE

    B->>Hub: kline event (BTCUSDT 15m, k.x = true)
    Hub->>R: Save finalized 15m candle in Redis
    Hub->>R: Enqueue Celery tasks for 50 active accounts

    par Parallel execution across Celery workers
        R->>W1: Task run_account_symbol(nitin, BTCUSDT, 15m)
        W1->>R: Read 15m candles from Redis (0 REST calls)
        W1->>W1: Compute EMA & pre-flight checks
        W1->>DB: Write SignalLog (HOLD / Paper Trade)
    and
        R->>W2: Task run_account_symbol(amol, BTCUSDT, 15m)
        W2->>R: Read 15m candles from Redis (0 REST calls)
        W2->>W2: Compute EMA & pre-flight checks
        W2->>Ex: Place market order (Demo / Live)
        Ex-->>W2: Order fill (orderId, executedQty)
        W2->>DB: Atomic Trade + BotConfig.current_cash + SignalLog
        W2->>R: Publish trade/signal event
    end

    R-->>UI: Push new Trade & SignalLog row via SSE
```

---

## 3. Why This Scales to 50+ Accounts (300+ Bots)

### 1. Zero Duplicate Market REST or WebSocket Connections
- Binance rate limits (`2,400 weight/min` on Futures) are enforced **per server IP**, not per account.
- **Process 1 (`ws_hub`)** maintains **one** market WebSocket per `(environment, market)` regardless of whether you have 2 accounts or 50 accounts, and stores candles/prices in **Redis**.
- Neither Celery workers nor the Django web server ever need to hit Binance REST for market data.

### 2. True Parallel Execution via Celery Workers
- Instead of `run_all_active_bots()` in [tasks.py](file:///Users/amolc/2026/stockgame/trading/tasks.py) looping sequentially through 300 bots, `ws_hub` enqueues one Celery task per active `(account_id, symbol)` on candle close.
- **4–8 concurrent Celery workers** process all 50 accounts in parallel in `< 1s`, with automatic retries and fault isolation (one account's API error never delays the other 49).
- PostgreSQL (`db.quantbots.co` in [settings.py](file:///Users/amolc/2026/stockgame/stockgame/settings.py)) handles concurrent Celery writes cleanly.

### 3. Pre-Flight Exchange Filter Validation (`MIN_NOTIONAL` & `LOT_SIZE`)
- `BinanceService.round_quantity()` ([binance_service.py](file:///Users/amolc/2026/stockgame/trading/services/binance_service.py)) and `ExecutionService` ([execution_service.py](file:///Users/amolc/2026/stockgame/trading/services/execution_service.py)) should validate both `LOT_SIZE` and `MIN_NOTIONAL` (from cached `exchangeInfo`) *before* calling `place_market_order()`.
- Prevents wasted REST weight and `-4164` rejected-order errors across 300 bots.

### 4. Thin Django Web Server + SSE Push
- The web server (`stockgame`) reads live prices, candles, and balances directly from **Redis** and pushes deltas to the browser via Server-Sent Events (SSE), eliminating redundant WebSockets and 1s browser polling.

### 5. Safety-Net Reconciliation Sweep
- Keep a lightweight `5m` Celery Beat periodic task that checks if any active bot missed a candle close during a WebSocket reconnect, using `SignalLog` deduplication so already-evaluated candles are skipped.
