# StockGame - 24/7 Binance Algorithmic Trading Platform

A production-grade algorithmic trading platform built with **Django**, **Binance REST API**, **Plotly**, and **PM2**.
Features automated background trading tasks, EMA 20/50 crossover detection with long/short reversal, database persistence, and an interactive web dashboard.

---

## 🏗️ Architecture

```text
c:\projects\trade game\
├── manage.py                   # Django CLI management entry point
├── ecosystem.config.js         # PM2 production process configuration
├── stockgame/                  # Django project configuration (settings, urls, wsgi)
├── trading/                    # Core trading engine app
│   ├── models.py               # BotConfig, Trade, SignalLog
│   ├── tasks.py                # Core scheduled trading task (run_trading_cycle)
│   ├── services/
│   │   ├── binance_service.py  # High-speed Binance API client (public + auth modes)
│   │   ├── strategy_service.py # EMA 20/50 Crossover signal detector
│   │   └── execution_service.py# Order execution & paper trading accounting
│   ├── management/commands/
│   │   ├── run_bot.py          # 24/7 background worker daemon (managed via PM2)
│   │   └── test_cycle.py       # One-shot CLI test command
│   ├── views.py                # Web dashboard, REST API, manual triggers
│   ├── urls.py                 # URL routing
│   └── admin.py                # Full-featured Django Admin
├── templates/trading/          # Modern dark-themed dashboard template
├── app.py                      # Streamlit interactive chart viewer
└── requirements.txt            # Dependencies
```

---

## 🚀 How to Test & Run

### 1. Test Single Cycle (CLI)
Run a single trading cycle to verify Binance connection, EMA calculations, and database logging:
```bash
.\venv\Scripts\python.exe manage.py test_cycle
```

### 2. Run the 24/7 Background Trading Worker Daemon
Runs continuously in the background, checking candles and executing crossover trades:
```bash
.\venv\Scripts\python.exe manage.py run_bot --interval 30
```

### 3. Start the Django Web Server
```bash
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8080
```
- **Web Dashboard:** [http://127.0.0.1:8080/](http://127.0.0.1:8080/)
- **Django Admin:** [http://127.0.0.1:8080/admin/](http://127.0.0.1:8080/admin/)  
  *(Default Credentials: Username: `admin` | Password: `admin123`)*
- **JSON API Status:** [http://127.0.0.1:8080/api/status/](http://127.0.0.1:8080/api/status/)

---

## ⚙️ Running 24/7 with PM2 (Production)

To run both the web server and the background trading task in production using PM2:

```bash
# 1. Start all services in the background
pm2 start ecosystem.config.js

# 2. Check live status and uptime
pm2 status

# 3. View live trade execution logs
pm2 logs stockgame-worker

# 4. Stop all processes
pm2 stop all
```

---

## 🔑 Plug-and-Play API Keys (Optional)
The system runs in **Free Public Mode** by default (pulls real Binance data, executes paper trades).  
To enable Binance **USDⓈ-M Futures** execution (Demo Trading, Testnet, or Live):
1. Copy `.env.example` to `.env`
2. Add your keys:
   ```env
   BINANCE_API_KEY=your_key
   BINANCE_API_SECRET=your_secret
   BINANCE_DEMO=true            # keys from demo.binance.com
   BINANCE_MARKET=futures       # futures | spot
   BINANCE_FUTURES_LEVERAGE=5
   # For testnet.binancefuture.com instead of demo:
   # BINANCE_DEMO=false
   # BINANCE_TESTNET=true
   ```
   The bot will automatically switch to **Authenticated Mode** and trade USDⓈ-M Futures with the configured leverage.

### 📡 Paper Mode → Demo Mirroring
PAPER bots keep simulated accounting, but can **also send every signal** (open/close/reverse) to the Binance Demo/Testnet account. Enable **Mirror to Demo** on the bot (or set `mirror_to_demo` in Django Admin). Mirroring only runs when `BINANCE_DEMO=true` or `BINANCE_TESTNET=true`; it is intentionally disabled for mainnet keys so paper mode can never place real orders. Demo order ids appear in trade notes as `Demo #<orderId>`.
