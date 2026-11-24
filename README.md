# Binance EMA Crypto Trading & Analytics Prototype

A standalone, modular Python prototype that connects directly to the **official Binance REST API**, calculates **Exponential Moving Average (EMA) crossovers**, simulates **paper trade execution**, and presents live metrics on an interactive **Streamlit dashboard**.

---

## Architecture & Features

```
trade game/
├── venv/                       # Isolated virtual environment
├── requirements.txt            # python-binance, pandas, numpy, plotly, streamlit, python-dotenv
├── .env.example                # Config template for optional Binance API keys
├── src/
│   ├── __init__.py
│   ├── binance_client.py       # Direct Binance API connector (Public & Authenticated modes)
│   ├── indicators.py           # EMA 20, 50, 100, 200 & RSI indicator math
│   ├── strategy.py             # EMA Crossover signal generator (BUY / SELL triggers)
│   └── simulator.py            # Paper trading engine (tracks $10,000 cash, trades, PnL, Win Rate)
├── test_pipeline.py            # Standalone terminal test script
└── app.py                      # Interactive Streamlit Web Dashboard
```

### Highlights:
1. **Direct Binance API Ingestion:** No unofficial scraping or TradingView middleman. Fast, reliable, and 100% free.
2. **Zero API Key Needed for Public Mode:** Pulls real-time prices and historical OHLCV candles without any account or API keys.
3. **Plug-and-Play for Live Orders:** Includes `.env` configuration ready to accept Binance Testnet or Live API keys whenever real order routing is required.
4. **EMA Crossover Strategy:** Detects Golden Crosses (Buy) and Death Crosses (Sell).
5. **Full Paper Trading Simulator:** Simulates order fills, tracks virtual cash, calculates trade win-rate, profit factor, drawdown, and dollar PnL.
6. **Interactive Dashboard:** Dynamic Plotly candlestick charts with `▲ BUY` (green) and `▼ SELL` (red) execution markers.

---

## How to Run

### 1. Run Terminal Test:
```bash
.\venv\Scripts\python.exe test_pipeline.py
```

### 2. Launch the Streamlit Web Dashboard:
```bash
.\venv\Scripts\streamlit.exe run app.py
```
Open **[http://localhost:8501](http://localhost:8501)** in your web browser.

---

## Plug-and-Play API Keys (Optional)
When you are ready to test live order routing or testnet:
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Add your Binance API key and secret:
   ```env
   BINANCE_API_KEY=your_key_here
   BINANCE_API_SECRET=your_secret_here
   BINANCE_TESTNET=true
   ```
3. The app will automatically switch from **Public Mode** to **Authenticated Mode**.
