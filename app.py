"""
Binance Futures Trading & Analytics Dashboard (Streamlit - Port 8501)
Live Binance Demo Futures Manual Trading Terminal & Chart Analytics.
Directly uses core trading functions from trading.services.binance_trader:
  - getdata()
  - positions()
  - buy()
  - buyclose()
  - sell()
  - sellclose()
  - reset()
Preserves chart zoom/pan state across auto-refreshes using Plotly's uirevision.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

# 1. Setup Django environment for database access
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stockgame.settings")
import django
django.setup()

from trading.services.binance_trader import BinanceTrader
from trading.models import TradingAccount, BotConfig, Trade, SignalLog
from src.strategy import generate_crossover_signals

load_dotenv()

# 2. Page Configuration
st.set_page_config(
    page_title="Binance Demo Futures Trading Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Dark Styling
st.markdown("""
<style>
    .metric-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 14px;
        text-align: center;
    }
    .metric-value {
        font-size: 22px;
        font-weight: 700;
        color: #f0f6fc;
    }
    .metric-label {
        font-size: 12px;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .stButton>button {
        border-radius: 6px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)


# 3. Singleton Trader Instance per Account/Bot (Reuses HTTPS connection pool)
@st.cache_resource
def get_trader_instance(bot_id=None):
    bot = BotConfig.objects.select_related("account").filter(id=bot_id).first() if bot_id else None
    return BinanceTrader(bot_config=bot)


# 4. Sidebar Configuration
st.sidebar.title("Terminal Settings")

accounts = list(TradingAccount.objects.all().order_by("id"))
selected_account = None
if accounts:
    acct_names = [f"{a.name} ({a.environment})" for a in accounts]
    acct_idx = st.sidebar.selectbox("Trading Account", range(len(accounts)), format_func=lambda i: acct_names[i], index=0)
    selected_account = accounts[acct_idx]

st.sidebar.subheader("Market Instrument")
preset_symbols = ["BTCUSDT", "SOXLUSDT", "QQQUSDT", "ETHUSDT", "NVDAUSDT", "TSLAUSDT", "Custom"]
selected_symbol_preset = st.sidebar.selectbox("Symbol", preset_symbols, index=0)
if selected_symbol_preset == "Custom":
    symbol = st.sidebar.text_input("Enter Pair", "BTCUSDT").strip().upper()
else:
    symbol = selected_symbol_preset

selected_bot = None
if selected_account is not None:
    selected_bot = BotConfig.objects.filter(account=selected_account, symbol=symbol).first() or BotConfig.objects.filter(account=selected_account).first()
trader = get_trader_instance(selected_bot.id if selected_bot else None)

interval = st.sidebar.selectbox(
    "Candle Interval",
    options=["1m", "5m", "15m", "1h", "4h", "1d"],
    index=0  # Default 1m for active real-time viewing
)

lookback_options = {
    "1 Day": "1 day ago UTC",
    "3 Days": "3 days ago UTC",
    "7 Days": "7 days ago UTC",
    "14 Days": "14 days ago UTC",
    "30 Days": "30 days ago UTC"
}
selected_lookback_label = st.sidebar.selectbox("Lookback Window", list(lookback_options.keys()), index=0)
lookback = lookback_options[selected_lookback_label]

st.sidebar.subheader("Strategy Indicators")
fast_ema = st.sidebar.slider("Fast EMA Period", min_value=5, max_value=50, value=20, step=1)
slow_ema = st.sidebar.slider("Slow EMA Period", min_value=15, max_value=200, value=50, step=1)

st.sidebar.subheader("📡 Live Stream Control")
auto_refresh = st.sidebar.checkbox("Auto-Refresh Candlesticks", value=True)
refresh_interval = st.sidebar.slider("Refresh Interval (seconds)", min_value=1, max_value=60, value=2, step=1)

refresh_btn = st.sidebar.button("🔄 Manual Refresh Now", use_container_width=True)


# Initialize session state for zoom controls
if "zoom_version" not in st.session_state:
    st.session_state.zoom_version = 0

zoom_key = f"{symbol}_{interval}_{st.session_state.zoom_version}"

# 5. Header Section
header_col1, header_col2 = st.columns([3, 1])
with header_col1:
    st.title("⚡ Binance Demo Futures Manual Trading Terminal")
    st.caption("Live Demo Futures API Execution • Persistent Chart Zoom • Direct `binance_trader.py` Core Engine")

with header_col2:
    if trader.is_authenticated:
        st.success("🟢 Authenticated (Binance Demo Futures)")
    else:
        st.warning("⚠️ Public / Paper Mode (No API Keys)")


# 6. Live Fragment: Auto-Refreshing Market View & Chart
run_every_param = refresh_interval if auto_refresh else None


@st.fragment(run_every=run_every_param)
def render_terminal():
    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    # Fetch live account positions and balances from binance_trader
    pos_data = trader.positions(symbol=symbol)
    current_price = pos_data["live_price"]
    has_pos = pos_data["has_open_position"]
    open_pos = pos_data["open_position"]

    # Top KPI Metrics Row
    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)

    with kpi1:
        st.metric(
            label=f"Live {symbol} Price",
            value=f"${current_price:,.2f}"
        )

    with kpi2:
        usdt_bal = pos_data.get("real_usdt_balance")
        usdt_val = f"${usdt_bal:,.2f}" if usdt_bal is not None else f"${pos_data['wallet_cash']:,.2f}"
        st.metric(label="Available USDT (Cash)", value=usdt_val)

    with kpi3:
        asset_sym = symbol.replace("USDT", "")
        coin_bal = pos_data.get("real_asset_balance")
        coin_val = f"{coin_bal:,.5f} {asset_sym}" if coin_bal is not None else "0.00000"
        st.metric(label=f"Wallet {asset_sym} Balance", value=coin_val)

    with kpi4:
        if has_pos and open_pos:
            st.metric(
                label="Active Position",
                value=f"{open_pos.get('side', 'LONG')}: {open_pos['quantity']:.5f} {asset_sym}",
                delta=f"Entry: ${open_pos['entry_price']:,.2f}"
            )
        else:
            st.metric(label="Active Position", value="⚪ FLAT (No Position)")

    with kpi5:
        if has_pos and open_pos:
            pnl = open_pos["unrealized_pnl"]
            pnl_pct = open_pos["unrealized_pnl_pct"]
            st.metric(
                label="Floating Unrealized PnL",
                value=f"${pnl:+,.2f}",
                delta=f"{pnl_pct:+.2f}%"
            )
        else:
            st.metric(label="Floating Unrealized PnL", value="$0.00", delta="0.00%")

    st.markdown("---")

    # Main Grid: Left Column (Chart) & Right Column (Manual Trading Panel)
    col_chart, col_trade = st.columns([3, 1])

    # --- LEFT COLUMN: LIVE CHART WITH PERSISTENT ZOOM ---
    with col_chart:
        # Chart toolbar row: stream timestamp & reset zoom button
        tb_col1, tb_col2 = st.columns([4, 1])
        with tb_col1:
            st.caption(f"📡 **Live Demo Futures Stream** ({interval}) • Last update: **{now_utc}** • *Mouse wheel zooms, double-click resets view*")
        with tb_col2:
            if st.button("🔍 Reset Zoom", key="btn_reset_zoom", use_container_width=True):
                st.session_state.zoom_version += 1
                st.rerun()

        try:
            # 1. getdata() from binance_trader
            raw_df = trader.getdata(symbol=symbol, interval=interval, lookback=lookback)
            # Calculate EMAs & Signals
            df = generate_crossover_signals(raw_df, fast_period=fast_ema, slow_period=slow_ema)

            fig = go.Figure()

            # Candlestick Series
            fig.add_trace(go.Candlestick(
                x=df.index,
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name=f"{symbol}",
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

            # Render Actual Database Trade Entries & Exits on Chart
            recent_trades = Trade.objects.filter(symbol=symbol).order_by('-entry_time')[:15]
            for tr in recent_trades:
                fig.add_trace(go.Scatter(
                    x=[tr.entry_time],
                    y=[float(tr.entry_price)],
                    mode="markers+text",
                    name=f"Trade #{tr.id} Buy",
                    text=[f"BUY #{tr.id}"],
                    textposition="bottom center",
                    marker=dict(symbol="triangle-up", size=13, color="#00E676", line=dict(width=1, color="#ffffff")),
                    showlegend=False
                ))
                if tr.exit_time and tr.exit_price:
                    fig.add_trace(go.Scatter(
                        x=[tr.exit_time],
                        y=[float(tr.exit_price)],
                        mode="markers+text",
                        name=f"Trade #{tr.id} Close",
                        text=[f"SELL #{tr.id}"],
                        textposition="top center",
                        marker=dict(symbol="triangle-down", size=13, color="#FF1744", line=dict(width=1, color="#ffffff")),
                        showlegend=False
                    ))

            # CRITICAL ZOOM LOCK:
            # 1. Static title (timestamp kept outside in st.caption)
            # 2. uirevision locked on layout, xaxis, and yaxis
            # 3. autorange=None so user-dragged axes are NEVER reset
            current_zoom_key = f"{symbol}_{interval}_{st.session_state.zoom_version}"
            fig.update_layout(
                uirevision=current_zoom_key,
                title=f"{symbol} Real-Time Chart ({interval})",
                xaxis=dict(
                    uirevision=current_zoom_key,
                    rangeslider=dict(visible=False),
                    type="date"
                ),
                yaxis=dict(
                    uirevision=current_zoom_key,
                    fixedrange=False
                ),
                template="plotly_dark",
                height=560,
                margin=dict(l=30, r=30, t=40, b=30),
                legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1)
            )

            st.plotly_chart(
                fig,
                use_container_width=True,
                key=f"candlestick_chart_{symbol}_{interval}",
                config={
                    "scrollZoom": True,
                    "displayModeBar": True,
                    "displaylogo": False,
                    "modeBarButtonsToAdd": ["drawline", "eraseshape"]
                }
            )

        except Exception as e:
            st.error(f"Error streaming candlesticks from Binance: {e}")

    # --- RIGHT COLUMN: MANUAL TRADING CONTROLS (binance_trader actions) ---
    with col_trade:
        st.markdown("### 🎮 Manual Execution")
        st.caption("Executes real demo futures orders on Binance via `BinanceTrader`.")

        spend_usdt = st.number_input(
            "Order Amount (USDT)",
            min_value=10.0,
            max_value=10000.0,
            value=50.0,
            step=10.0,
            help="Minimum Binance order size is $10 USDT."
        )

        approx_qty = round(spend_usdt / current_price, 5) if current_price else 0.0
        st.info(f"Est. Size: **~{approx_qty:.5f} {asset_sym}**")

        st.markdown("---")

        # 1. BUY Action (calls trader.buy)
        buy_disabled = has_pos
        if st.button("🟢 BUY (Open Long)", use_container_width=True, disabled=buy_disabled, type="primary"):
            with st.spinner(f"Placing Demo Futures BUY for ${spend_usdt:,.2f} USDT..."):
                res = trader.buy(symbol=symbol, amount_usdt=spend_usdt)
                if res.get("ok"):
                    st.success(f"✅ BUY SUCCESS! Order #{res.get('binance_order_id')}")
                    st.toast(f"Bought {res.get('quantity')} {symbol} @ ${res.get('price'):,.2f}!", icon="🟢")
                    st.rerun()
                else:
                    st.error(f"❌ {res.get('error')}")

        # 2. BUYCLOSE Action (calls trader.buyclose) - only when LONG
        is_long = bool(has_pos and open_pos and open_pos.get('side') == 'LONG')
        is_short = bool(has_pos and open_pos and open_pos.get('side') == 'SHORT')
        close_disabled = not is_long
        if st.button("🔴 BUYCLOSE (Close Long)", use_container_width=True, disabled=close_disabled):
            with st.spinner(f"Closing Long position in {symbol}..."):
                res = trader.buyclose(symbol=symbol)
                if res.get("ok"):
                    pnl_val = res.get('realized_pnl', 0)
                    pnl_pct_val = res.get('realized_pnl_pct', 0)
                    st.success(f"✅ Position Closed! Realized PnL: ${pnl_val:+,.2f} ({pnl_pct_val:+.2f}%)")
                    st.toast(f"Closed @ ${res.get('exit_price'):,.2f} (PnL: ${pnl_val:+,.2f})", icon="🔴")
                    st.rerun()
                else:
                    st.error(f"❌ {res.get('error')}")

        # Advanced Actions Expander: sell, sellclose, reset
        with st.expander("🛠️ More Actions (Short / Reset)", expanded=True):
            st.caption("Open/close SHORT futures positions in `binance_trader.py`:")

            if st.button("🔻 SELL (Open Short)", use_container_width=True, disabled=has_pos):
                with st.spinner(f"Opening SHORT for ${spend_usdt:,.2f} USDT..."):
                    res = trader.sell(symbol=symbol, amount_usdt=spend_usdt)
                    if res.get("ok"):
                        st.success(f"✅ SHORT OPEN! Order #{res.get('binance_order_id')}")
                        st.toast(f"Short {res.get('quantity')} {symbol} @ ${res.get('price'):,.2f}!", icon="🔻")
                        st.rerun()
                    else:
                        st.error(f"❌ {res.get('error')}")

            if st.button("🔄 SELLCLOSE (Cover Short)", use_container_width=True, disabled=not is_short):
                with st.spinner(f"Covering SHORT in {symbol}..."):
                    res = trader.sellclose(symbol=symbol)
                    if res.get("ok"):
                        pnl_val = res.get('realized_pnl', 0)
                        pnl_pct_val = res.get('realized_pnl_pct', 0)
                        st.success(f"✅ Short Covered! Realized PnL: ${pnl_val:+,.2f} ({pnl_pct_val:+.2f}%)")
                        st.rerun()
                    else:
                        st.error(f"❌ {res.get('error')}")

            st.markdown("---")
            if st.button("⚠️ Reset All Test Trades", use_container_width=True):
                trader.reset()
                st.warning("Cleared local test trades and restored wallet balance.")
                st.rerun()

    # --- BOTTOM SECTION: REAL DATABASE TRADE HISTORY ---
    st.markdown("---")
    tab_trades, tab_signals = st.tabs(["📋 Real Demo Futures Trades (Database)", "📡 Signal Logs"])

    with tab_trades:
        trades_qs = Trade.objects.filter(bot_config_id=trader.bot.id).order_by('-entry_time')[:30]
        if trades_qs.exists():
            rows = []
            for t in trades_qs:
                rows.append({
                    "Trade ID": f"#{t.id}",
                    "Symbol": t.symbol,
                    "Side": t.side,
                    "Status": "🟢 OPEN" if t.status == 'OPEN' else "⚪ CLOSED",
                    "Entry Time": t.entry_time.strftime("%Y-%m-%d %H:%M:%S") if t.entry_time else "-",
                    "Entry Price": f"${t.entry_price:,.2f}",
                    "Quantity": f"{t.quantity:.5f}",
                    "Cost (USDT)": f"${t.cost:,.2f}",
                    "Exit Price": f"${t.exit_price:,.2f}" if t.exit_price else "-",
                    "Realized PnL": f"${t.pnl:+,.2f} ({t.pnl_pct:+.2f}%)" if t.pnl is not None else "-",
                    "Result": "WIN ✅" if t.is_win else ("LOSS ❌" if t.is_win is False else "-"),
                    "Notes / Order ID": t.notes
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.info("No recorded trades yet. Click 'BUY' above to place your first Demo Futures trade!")

    with tab_signals:
        signals_qs = SignalLog.objects.filter(bot_config_id=trader.bot.id).order_by('-timestamp')[:20]
        if signals_qs.exists():
            sig_rows = [
                {
                    "Time": s.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "Symbol": s.symbol,
                    "Signal": s.signal_type,
                    "Price": f"${s.price:,.2f}",
                    "Executed": "YES ✅" if s.executed else "NO ⚪",
                    "Message": s.message
                }
                for s in signals_qs
            ]
            st.dataframe(pd.DataFrame(sig_rows), use_container_width=True)
        else:
            st.info("No signal logs recorded yet.")


# Execute the live fragment
render_terminal()
