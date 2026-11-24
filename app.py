"""
Binance Crypto Trading & Analytics Dashboard
Built with Streamlit, Plotly, Pandas, and python-binance.
Features real Binance market data, EMA Crossover strategy, and Paper Trading simulator.
Supports automatic live background refresh via modern st.fragment.
"""

from datetime import datetime, timezone
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from src.binance_client import BinanceDataClient
from src.strategy import generate_crossover_signals
from src.simulator import PaperTradingSimulator

# 1. Page Configuration
st.set_page_config(
    page_title="Binance Crypto Trading & EMA Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .metric-card {
        background-color: #0e1117;
        border: 1px solid #262730;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
    }
    .metric-value {
        font-size: 24px;
        font-weight: 700;
    }
    .metric-label {
        font-size: 13px;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
</style>
""", unsafe_allow_html=True)

# 2. Sidebar Controls
st.sidebar.title("⚙️ Trading Configuration")

# Market Data Settings
st.sidebar.subheader("Market Instrument")
preset_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "Custom"]
selected_symbol_preset = st.sidebar.selectbox("Symbol", preset_symbols, index=0)
if selected_symbol_preset == "Custom":
    symbol = st.sidebar.text_input("Enter Pair", "BTCUSDT").strip().upper()
else:
    symbol = selected_symbol_preset

interval = st.sidebar.selectbox(
    "Timeframe Interval",
    options=["1m", "5m", "15m", "1h", "4h", "1d"],
    index=0  # Default 1m for fastest real-time action!
)

lookback_options = {
    "1 Day": "1 day ago UTC",
    "3 Days": "3 days ago UTC",
    "7 Days": "7 days ago UTC",
    "14 Days": "14 days ago UTC",
    "30 Days": "30 days ago UTC"
}
selected_lookback_label = st.sidebar.selectbox("Lookback Horizon", list(lookback_options.keys()), index=0)
lookback = lookback_options[selected_lookback_label]

# Strategy Settings
st.sidebar.subheader("EMA Strategy Parameters")
fast_ema = st.sidebar.slider("Fast EMA Period", min_value=5, max_value=50, value=20, step=1)
slow_ema = st.sidebar.slider("Slow EMA Period", min_value=15, max_value=200, value=50, step=1)

if fast_ema >= slow_ema:
    st.sidebar.warning("⚠️ Fast EMA should be smaller than Slow EMA for valid crossover signals.")

# Simulator Settings
st.sidebar.subheader("Simulation Account")
initial_capital = st.sidebar.number_input("Starting Capital (USDT)", min_value=100.0, max_value=1000000.0, value=10000.0, step=500.0)

# Auto-Refresh Settings
st.sidebar.subheader("📡 Live Data Stream")
auto_refresh = st.sidebar.checkbox("Auto-Refresh Automatically", value=True)
refresh_interval = st.sidebar.slider("Refresh Every (seconds)", min_value=3, max_value=60, value=5, step=1)

refresh_btn = st.sidebar.button("🔄 Manual Refresh Now", use_container_width=True)

# 3. Initialize Binance Client & Connection Status
@st.cache_resource
def get_client():
    return BinanceDataClient()

client = get_client()

# Header Section
header_col1, header_col2 = st.columns([3, 1])
with header_col1:
    st.title("📈 Binance EMA Algorithmic Dashboard")
    st.caption("Direct Binance API Data • EMA 20/50 Crossover Signals • Paper Trading Execution")

with header_col2:
    if client.is_authenticated():
        st.success("🟢 Authenticated Mode (API Keys Active)")
    else:
        st.info("🌐 Public Mode (Real Binance Data • Paper Simulation)")

# 4. Live Dashboard Rendering Fragment
run_every_param = refresh_interval if auto_refresh else None

@st.fragment(run_every=run_every_param)
def render_live_dashboard():
    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    
    # Live status badge
    stream_badge = f"🟢 **LIVE STREAM ACTIVE** (Auto-refreshing every {refresh_interval}s • Last: {now_utc})" if auto_refresh else f"⏸️ **AUTO-REFRESH PAUSED** (Last: {now_utc})"
    st.markdown(stream_badge)

    try:
        # Fetch OHLCV data directly from Binance
        raw_df = client.get_klines(symbol=symbol, interval=interval, lookback=lookback)
        
        # Calculate EMAs and Crossover Signals
        df = generate_crossover_signals(raw_df, fast_period=fast_ema, slow_period=slow_ema)
        
        # Run Simulator
        simulator = PaperTradingSimulator(initial_capital=initial_capital)
        metrics = simulator.run(df)

        latest_close = df["close"].iloc[-1]
        prev_close = df["close"].iloc[-2] if len(df) > 1 else latest_close
        price_change = latest_close - prev_close
        price_change_pct = (price_change / prev_close) * 100.0 if prev_close else 0.0

        # Top KPI Summary Cards
        kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)

        with kpi1:
            st.metric(
                label=f"Latest {symbol} Price",
                value=f"${latest_close:,.2f}",
                delta=f"{price_change_pct:+.2f}%"
            )

        with kpi2:
            status_label = "🟢 IN TRADE (LONG)" if metrics["is_in_position"] else "⚪ CASH (WAITING)"
            st.metric(label="Bot Position Status", value=status_label)

        with kpi3:
            st.metric(
                label="Total Strategy PnL",
                value=f"${metrics['total_pnl']:+,.2f}",
                delta=f"{metrics['total_return_pct']:+.2f}%"
            )

        with kpi4:
            st.metric(
                label="Win Rate",
                value=f"{metrics['win_rate']:.1f}%",
                delta=f"{metrics['winning_trades']}/{metrics['total_trades']} Won"
            )

        with kpi5:
            st.metric(
                label="Max Drawdown",
                value=f"-{metrics['max_drawdown']:.2f}%",
                delta=f"Profit Factor: {metrics['profit_factor']:.2f}"
            )

        st.markdown("---")

        # Interactive Candlestick Chart with Plotly
        fig = go.Figure()

        # Candlestick Series
        fig.add_trace(go.Candlestick(
            x=df.index,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=f"{symbol} Candlesticks",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350"
        ))

        # Fast EMA
        fig.add_trace(go.Scatter(
            x=df.index,
            y=df[f"EMA_{fast_ema}"],
            mode="lines",
            name=f"Fast EMA ({fast_ema})",
            line=dict(color="#00E5FF", width=1.5)
        ))

        # Slow EMA
        fig.add_trace(go.Scatter(
            x=df.index,
            y=df[f"EMA_{slow_ema}"],
            mode="lines",
            name=f"Slow EMA ({slow_ema})",
            line=dict(color="#FF9100", width=1.5)
        ))

        # Buy Signals (Triangles Up)
        buys = df[df["signal"] == "BUY"]
        if not buys.empty:
            fig.add_trace(go.Scatter(
                x=buys.index,
                y=buys["signal_price"] * 0.998,
                mode="markers+text",
                name="BUY Signal",
                text="BUY",
                textposition="bottom center",
                marker=dict(symbol="triangle-up", size=14, color="#00E676", line=dict(width=1, color="#ffffff"))
            ))

        # Sell Signals (Triangles Down)
        sells = df[df["signal"] == "SELL"]
        if not sells.empty:
            fig.add_trace(go.Scatter(
                x=sells.index,
                y=sells["signal_price"] * 1.002,
                mode="markers+text",
                name="SELL Signal",
                text="SELL",
                textposition="top center",
                marker=dict(symbol="triangle-down", size=14, color="#FF1744", line=dict(width=1, color="#ffffff"))
            ))

        fig.update_layout(
            title=f"{symbol} Real-Time Chart with EMA {fast_ema}/{slow_ema} Crossovers",
            xaxis_title="Time (UTC)",
            yaxis_title="Price (USDT)",
            template="plotly_dark",
            height=620,
            margin=dict(l=40, r=40, t=50, b=40),
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )

        st.plotly_chart(fig, use_container_width=True)

        # Analytics & Trade Log Tabs
        tab1, tab2, tab3 = st.tabs(["📋 Executed Trade History", "📈 Equity Growth Curve", "📊 Raw Binance OHLCV Bars"])

        with tab1:
            if metrics["trades"]:
                trades_df = pd.DataFrame(metrics["trades"])
                trades_df["entry_time"] = pd.to_datetime(trades_df["entry_time"]).dt.strftime("%Y-%m-%d %H:%M")
                trades_df["exit_time"] = pd.to_datetime(trades_df["exit_time"]).dt.strftime("%Y-%m-%d %H:%M")
                trades_df["entry_price"] = trades_df["entry_price"].map("${:,.2f}".format)
                trades_df["exit_price"] = trades_df["exit_price"].map("${:,.2f}".format)
                trades_df["pnl"] = trades_df["pnl"].map("${:+,.2f}".format)
                trades_df["pnl_pct"] = trades_df["pnl_pct"].map("{:+.2f}%".format)
                trades_df["result"] = trades_df["is_win"].apply(lambda w: "WIN ✅" if w else "LOSS ❌")

                display_cols = ["entry_time", "entry_price", "exit_time", "exit_price", "pnl", "pnl_pct", "result"]
                st.dataframe(trades_df[display_cols], use_container_width=True)
            else:
                st.info("No completed round-trip trades in this timeframe window yet. Try expanding the lookback horizon or shortening the EMA periods.")

        with tab2:
            equity_df = metrics["equity_df"]
            if not equity_df.empty:
                eq_fig = go.Figure()
                eq_fig.add_trace(go.Scatter(
                    x=equity_df["datetime"],
                    y=equity_df["equity"],
                    mode="lines",
                    name="Account Equity",
                    line=dict(color="#00E5FF", width=2)
                ))
                eq_fig.add_hline(
                    y=initial_capital,
                    line_dash="dash",
                    line_color="gray",
                    annotation_text=f"Initial Capital: ${initial_capital:,.2f}"
                )
                eq_fig.update_layout(
                    title="Simulated Account Equity Over Time",
                    xaxis_title="Time",
                    yaxis_title="Account Equity (USDT)",
                    template="plotly_dark",
                    height=350,
                    margin=dict(l=40, r=40, t=40, b=40)
                )
                st.plotly_chart(eq_fig, use_container_width=True)

        with tab3:
            st.write(f"Showing latest {len(df)} candles fetched directly from Binance:")
            st.dataframe(df.tail(50), use_container_width=True)

    except Exception as e:
        st.error(f"❌ Error fetching or processing Binance data: {e}")
        st.info("Tip: Check your internet connection or verify the symbol name format (e.g., 'BTCUSDT').")

# Execute the live fragment
render_live_dashboard()
