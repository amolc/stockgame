# Function Reference

Complete reference of every function and method in the StockGame project, grouped by module.

Notation: `name(param: type = default) -> ReturnType`

---

## Table of Contents

- [Core Strategy Engine (`src/`)](#core-strategy-engine-src)
  - [indicators.py](#indicatorspy)
  - [strategy.py](#strategypy)
  - [binance_client.py](#binance_clientpy)
  - [simulator.py](#simulatorpy)
- [Trading App (`trading/`)](#trading-app-trading)
  - [models.py](#modelspy)
  - [tasks.py](#taskspy)
  - [services/binance_service.py](#servicesbinance_servicepy)
  - [services/strategy_service.py](#servicesstrategy_servicepy)
  - [services/execution_service.py](#servicesexecution_servicepy)
  - [services/binance_trader.py](#servicesbinance_traderpy)
  - [views.py](#viewspy)
  - [urls.py](#urlspy)
  - [admin.py](#adminpy)
- [Management Commands](#management-commands)
- [Scripts](#scripts)
- [Streamlit App (`app.py`)](#streamlit-app-apppy)

---

## Core Strategy Engine (`src/`)

These are framework-agnostic helpers used by the Streamlit app and the standalone simulator.

### indicators.py

| Function | Description |
| --- | --- |
| `add_emas(df, periods=[20, 50, 100, 200]) -> DataFrame` | Returns a copy of `df` with an `EMA_<period>` column added for each period, computed on the `close` column. |
| `calculate_rsi(df, period=14) -> Series` | Computes the Relative Strength Index (0–100) from the `close` column using an exponential moving average of gains/losses. |

### strategy.py

| Function | Description |
| --- | --- |
| `generate_crossover_signals(df, fast_period=20, slow_period=50) -> DataFrame` | Adds `EMA_<fast>`, `EMA_<slow>`, `signal` (`BUY`/`SELL`/`None`) and `signal_price` columns. A **BUY** is a golden cross (fast EMA crosses above slow); a **SELL** is a death cross. Unlike the Django `StrategyService`, this scans the **entire** series, so it is used for charting/backtesting. |

### binance_client.py

Class `BinanceDataClient(api_key=None, api_secret=None, testnet=False)` — thin public-data wrapper (Demo/Testnet/Live, Spot or Futures).

| Method | Description |
| --- | --- |
| `is_futures` *(property)* | True when `BINANCE_MARKET=futures`. |
| `is_authenticated() -> bool` | True when both an API key and secret are present. |
| `get_current_price(symbol="BTCUSDT") -> float` | Latest ticker price (futures or spot). |
| `get_klines(symbol="BTCUSDT", interval="15m", lookback="2 days ago UTC") -> DataFrame` | Downloads historical candles (futures: most recent 1500; spot: by `lookback`) and returns an OHLCV DataFrame indexed by UTC `datetime`. Raises `ValueError` if Binance returns nothing. |

### simulator.py

Class `PaperTradingSimulator(initial_capital=10000.0, fee_pct=0.001)` — backtests a signal series with virtual cash.

| Method | Description |
| --- | --- |
| `run(df) -> dict` | Walks the DataFrame row by row. Buys when `signal == "BUY"` and flat; sells when `signal == "SELL"` and holding. Tracks cash, positions and equity history, then returns the metrics dict. |
| `_calculate_metrics(latest_price) -> dict` | Builds the performance summary: final equity, total PnL/return, trade counts, win rate, profit factor, max drawdown, current position and trade list. |

---

## Trading App (`trading/`)

### models.py

**`BotConfig`** — a bot instance and its runtime configuration/state.

| Member | Description |
| --- | --- |
| `__str__() -> str` | Human-readable label with symbol, interval and active/paused status. |

Key fields: `symbol`, `interval`, `lookback`, `fast_ema_period`, `slow_ema_period`, `initial_capital`, `current_cash`, `order_amount_usdt`, `fee_pct`, `is_active`, `mode` (`PAPER`/`LIVE`), `mirror_to_demo`, `last_run_at`.

**`Trade`** — an open or closed position.

| Member | Description |
| --- | --- |
| `__str__() -> str` | Shows symbol, status, entry price and PnL when closed. |

Key fields: `bot_config`, `side` (`LONG`/`SHORT`), `status` (`OPEN`/`CLOSED`), `entry_time/price`, `quantity`, `cost`, `exit_time/price`, `net_revenue`, `pnl`, `pnl_pct`, `is_win`, `notes`.

**`SignalLog`** — every evaluated signal, used for deduplication and auditing.

| Member | Description |
| --- | --- |
| `__str__() -> str` | Formats timestamp, symbol, signal type and price. |

### tasks.py

| Function | Description |
| --- | --- |
| `run_trading_cycle_for_bot(bot) -> dict` | Runs one full cycle for a bot: fetch klines → evaluate EMA crossover → process execution → update `last_run_at`. Returns a summary dict (or `skipped (inactive)`). |
| `run_all_active_bots() -> list[dict]` | Loops over all active bots (creating a default bot if none exist), runs each cycle and collects results/errors per bot. |

### services/binance_service.py

Class `BinanceService(api_key="", api_secret="", testnet=False)` — authenticated Binance client used by the Django engine. Defaults to **USDⓈ-M Futures** (Demo Trading when `BINANCE_DEMO=true`).

| Method | Description |
| --- | --- |
| `is_authenticated` *(property)* | True when API key + secret are configured. |
| `is_futures` *(property)* | True when `BINANCE_MARKET=futures` (the default). |
| `fetch_klines(symbol="BTCUSDT", interval="15m", lookback="3 days ago UTC") -> DataFrame` | Fetches OHLCV history (futures klines use the most recent 1500 candles; spot uses `lookback`). Raises `ValueError` if empty. |
| `get_ticker_price(symbol="BTCUSDT") -> float` | Latest market price (futures or spot ticker). |
| `get_account_balances(symbol="BTCUSDT") -> dict` | Returns `{"usdt": ..., "asset": ...}`; for futures, USDT is the available margin balance and asset is the open position size. `None` values when unauthenticated. |
| `round_quantity(symbol, raw_qty) -> Decimal` | Floors a quantity to the symbol's `LOT_SIZE` `stepSize` (futures exchange info or spot symbol info; falls back to 5 decimals). |
| `set_leverage(symbol="BTCUSDT") -> dict | None` | Sets `BINANCE_FUTURES_LEVERAGE` on the futures symbol; no-op for spot/unauthenticated. |
| `place_market_order(symbol, side, quantity, reduce_only=False) -> dict` | Places a futures market order (with `reduceOnly` when closing) or a spot market order, depending on `BINANCE_MARKET`. |

### services/strategy_service.py

Class `StrategyService` — the live signal detector used by the bot.

| Method | Description |
| --- | --- |
| `calculate_emas(df, fast_period=20, slow_period=50) -> DataFrame` *(static)* | Adds `EMA_<fast>` and `EMA_<slow>` columns. |
| `evaluate_latest_signal(df, fast_period=20, slow_period=50) -> dict` *(classmethod)* | Evaluates only the **last completed candle** (`iloc[-2]` vs `iloc[-3]`) to avoid repainting. Returns `signal`, `timestamp`, `price`, `fast_ema`, `slow_ema`, `fast_prev`, `slow_prev`, `active_price`; or `{"signal": "NONE", "reason": ...}` when there is insufficient data. |

### services/execution_service.py

Class `ExecutionService`.

| Method | Description |
| --- | --- |
| `process_signal(bot_config, signal_data, binance=None) -> dict` *(static)* | Core execution logic for long/short futures. **BUY** closes any SHORT then opens LONG; **SELL** closes any LONG then opens SHORT. Deduplicates by closed-candle timestamp, blocks duplicate positions and undersized orders, and either paper-trades or places real futures market orders (leverage on entry, `reduceOnly` on close). Updates `Trade` and `BotConfig.current_cash` atomically, always writes a `SignalLog`, and returns `signal`, `executed`, `message`, `price`, `current_cash` (may also include `duplicate`). Position size is `order_amount_usdt` capped at available cash. |
| `_open_position(bot_config, binance, side, price, quantity, spend, candle_time)` *(static)* | Opens a LONG (BUY) or SHORT (SELL) trade, placing the matching futures order when in LIVE mode. Returns `(ok, message)`. |
| `_close_position(bot_config, open_trade, binance, candle_time, price)` *(static)* | Closes a LONG (reduce-only SELL) or SHORT (reduce-only BUY), computes side-aware PnL/fees, updates cash and returns `(ok, message)`. |

### services/binance_trader.py

Class `BinanceTrader(bot_config=None)` — the "6 core functions" interface used by the Streamlit app and scripts. Falls back to the first active bot (creating one if needed). Trades USDⓈ-M Futures by default (Demo/Testnet/Live per `.env`).

| Method | Description |
| --- | --- |
| `is_authenticated` *(property)* | True when API credentials are configured. |
| `is_futures` *(property)* | True when trading futures (`BINANCE_MARKET=futures`). |
| `getdata(symbol=None, interval=None, lookback="2 days ago UTC") -> DataFrame` | Fetches OHLCV candles for the bot's symbol/interval by default (futures or spot). |
| `get_current_price(symbol=None) -> float` | Live ticker price. |
| `_round_quantity(symbol, qty) -> Decimal` | Floors a quantity to the exchange `LOT_SIZE` step size. |
| `positions(symbol=None) -> dict` | Reports mode, live price, paper wallet cash, real balances/position size (if authenticated), and the open position with unrealized PnL. |
| `buy(symbol=None, amount_usdt=None) -> dict` | Opens a LONG. Guards against an existing position, orders below $10, and insufficient cash; sets leverage and places a real futures market order when authenticated, then records the `Trade` and `SignalLog`. |
| `buyclose(symbol=None) -> dict` | Closes the open LONG with a `reduceOnly` market order, realizes PnL, updates cash, and records a SELL signal log. |
| `sell(symbol=None, amount_usdt=None) -> dict` | Opens a SHORT futures position (market SELL). Guards against an existing position, orders below $10, insufficient cash, and Spot mode. |
| `sellclose(symbol=None) -> dict` | Closes the open SHORT with a `reduceOnly` BUY, computes side-aware PnL, updates cash, and records a BUY signal log. |
| `reset() -> dict` | Deletes the bot's trades and signal logs and restores `current_cash` to `initial_capital`. |

### views.py

| Function | Description |
| --- | --- |
| `_compute_kpis() -> dict` | Aggregates closed-trade KPIs: active/total bots, trade counts, win rate and total PnL. |
| `dashboard_view(request)` | Renders `trading/dashboard.html` with bots, recent trades/signals and KPIs. |
| `api_status_view(request)` | JSON endpoint (`/api/status/`) returning live prices, balances, open positions, KPIs, recent trades/signals and optional chart series (pass `?chart=0` to skip chart data). |
| `trigger_cycle_view(request)` | Runs `run_all_active_bots()` once and returns the results as JSON. |
| `update_bot_view(request, bot_id)` | POST JSON/form update of a bot's symbol, interval, mode, EMA periods, order size and active flag, with validation. |
| `toggle_bot_view(request, bot_id)` | POST that flips a bot's `is_active` flag. |

### urls.py

URL routes (`app_name = 'trading'`):

| Path | View | Name |
| --- | --- | --- |
| `` | `dashboard_view` | `dashboard` |
| `api/status/` | `api_status_view` | `api_status` |
| `api/trigger-cycle/` | `trigger_cycle_view` | `trigger_cycle` |
| `api/bot/<int:bot_id>/update/` | `update_bot_view` | `update_bot` |
| `api/bot/<int:bot_id>/toggle/` | `toggle_bot_view` | `toggle_bot` |

### admin.py

Django Admin registrations (no custom functions): `BotConfigAdmin`, `TradeAdmin`, `SignalLogAdmin` define `list_display`, `list_filter`, `search_fields` and `readonly_fields`.

---

## Management Commands

Run with `python manage.py <command>`.

| Command | Key methods / options | Description |
| --- | --- | --- |
| `run_bot` | `add_arguments` (`--interval`, default 30), `handle` | 24/7 daemon loop that calls `run_all_active_bots()` every N seconds until Ctrl-C. |
| `test_cycle` | `handle` | Runs one cycle and prints a DB snapshot (bots/trades/signals counts). |
| `manual_trade` | `add_arguments` (`--action` = buy/close/status/reset, `--amount`), `handle` | CLI manual trading utilities that write directly to the DB. |
| `test_trader` | `handle` | Exercises all 6 `BinanceTrader` functions in sequence and prints results. |

---

## Scripts

### scripts/test_binance.py

Module-level wrappers around `BinanceTrader` for use with `django-extensions runscript`. Each caches a singleton trader via `get_trader()`.

| Function | Description |
| --- | --- |
| `get_trader() -> BinanceTrader` | Returns a cached trader instance (reuses the HTTPS session). |
| `getdata(symbol="BTCUSDT", interval="15m", trader=None)` | Fetches and prints candles. |
| `positions(symbol="BTCUSDT", trader=None)` | Prints cash, position and unrealized PnL. |
| `buy(symbol="BTCUSDT", amount_usdt=5000.0, trader=None)` | Executes and reports a buy. |
| `buyclose(symbol="BTCUSDT", trader=None)` | Closes the long and reports realized PnL. |
| `sell(symbol="BTCUSDT", amount_usdt=5000.0, trader=None)` | Executes a short/sell. |
| `sellclose(symbol="BTCUSDT", trader=None)` | Covers a short. |
| `reset(trader=None)` | Clears trades/signals and restores wallet cash. |
| `run(*args)` | Dispatcher: `python manage.py runscript test_binance --script-args <action>` (`all` runs the full sequence). |

### scripts/binance_demo_trade.py

Standalone Binance Spot Testnet validation script.

| Function | Description |
| --- | --- |
| `get_binance_testnet_client() -> Client` | Builds an authenticated Testnet client; raises if keys are missing. |
| `check_connection(client=None) -> bool` | Pings the Testnet and prints server time/endpoint. |
| `check_balances(client=None) -> dict` | Prints non-zero account balances and returns the account payload. |
| `get_symbol_rules(symbol="BTCUSDT", client=None) -> dict` | Returns live price plus `LOT_SIZE` / `NOTIONAL` rules. |
| `test_order_buy(symbol="BTCUSDT", usdt_amount=50.0, client=None) -> bool` | Calls `create_test_order()` to validate an order without executing it. |
| `demo_buy(symbol="BTCUSDT", usdt_amount=50.0, client=None) -> dict` | Places a real Testnet market BUY and records the trade/signal in Django. |
| `demo_positions(symbol="BTCUSDT", client=None) -> dict` | Inspects the open DB position against the live Testnet price. |
| `demo_sell(symbol="BTCUSDT", client=None) -> dict` | Places a real Testnet market SELL, closes the trade and records realized PnL. |
| `run(*args)` | Dispatcher for the actions above, plus `all`. |

---

## Streamlit App (`app.py`)

`app.py` is a script-style dashboard, not an importable module. Its callable/feature units:

| Function | Description |
| --- | --- |
| `get_trader_instance()` | `@st.cache_resource` factory returning a cached `BinanceTrader`. |
| `render_terminal()` | `@st.fragment` auto-refreshing view: KPI metrics, live Plotly candlestick chart (with EMA overlays and DB trade markers), manual BUY/BUYCLOSE/SELL/SELLCLOSE/reset controls, and trade/signal history tables. |

It calls `BinanceTrader.getdata/positions/buy/buyclose/sell/sellclose/reset` and `src.strategy.generate_crossover_signals`.
