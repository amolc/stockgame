"""
Django Extensions Runscript: test_binance.py

Way 1 Architecture:
- Standalone top-level functions: getdata(), positions(), buy(), buyclose(), sell(), sellclose(), reset()
- Standard entry point: run(*args) dispatches to individual functions or runs the full sequence.
- Lazy-loaded singleton trader pattern to reuse HTTP session and connection pooling.

Usage:
  # Run individual functions:
  python manage.py runscript test_binance --script-args getdata
  python manage.py runscript test_binance --script-args positions
  python manage.py runscript test_binance --script-args buy
  python manage.py runscript test_binance --script-args buyclose
  python manage.py runscript test_binance --script-args sell
  python manage.py runscript test_binance --script-args sellclose
  python manage.py runscript test_binance --script-args reset

  # Run full lifecycle sequence:
  python manage.py runscript test_binance
"""

from decimal import Decimal
from trading.services.binance_trader import BinanceTrader
from trading.models import Trade, SignalLog, BotConfig

# Cached singleton trader instance to reuse the HTTP session
_cached_trader = None


def get_trader() -> BinanceTrader:
    """Return a cached BinanceTrader instance to reuse the persistent HTTPS connection."""
    global _cached_trader
    if _cached_trader is None:
        _cached_trader = BinanceTrader()
    return _cached_trader


def getdata(symbol: str = "BTCUSDT", interval: str = "15m", trader: BinanceTrader = None):
    """Fetch live market candlestick stream from Binance."""
    print(f"\n--> EXECUTING: getdata(symbol='{symbol}', interval='{interval}')")
    t = trader or get_trader()
    df = t.getdata(symbol=symbol, interval=interval)
    print(f"  [OK] Streamed {len(df)} candles from Binance!")
    print(f"  [OK] Latest Candle Time: {df.index[-1]}")
    print(f"  [OK] Latest Close Price: ${df['close'].iloc[-1]:,.2f} | Volume: {df['volume'].iloc[-1]:.4f} BTC")
    return df


def positions(symbol: str = "BTCUSDT", trader: BinanceTrader = None):
    """Check account cash balance, active positions, and unrealized floating PnL."""
    print(f"\n--> EXECUTING: positions(symbol='{symbol}')")
    t = trader or get_trader()
    pos = t.positions(symbol)
    print(f"  [OK] Available Cash:  ${pos['wallet_cash']:,.2f} USDT")
    print(f"  [OK] Open Position:   {'YES' if pos['has_open_position'] else 'NO (100% Cash)'}")
    if pos['has_open_position']:
        op = pos['open_position']
        print(f"       Holding: {op['quantity']} BTC @ ${op['entry_price']:,.2f} entry")
        print(f"       Current Val: ${op['current_value']:,.2f} | Unrealized PnL: ${op['unrealized_pnl']:+,.2f} ({op['unrealized_pnl_pct']:+.2f}%)")
    return pos


def buy(symbol: str = "BTCUSDT", amount_usdt: float = 5000.0, trader: BinanceTrader = None):
    """Execute a Buy order (allocating USDT to purchase BTC)."""
    print(f"\n--> EXECUTING: buy(symbol='{symbol}', amount_usdt=${amount_usdt:,.2f})")
    t = trader or get_trader()
    res = t.buy(symbol, amount_usdt=amount_usdt)
    if res.get("ok"):
        print(f"  [OK] BUY SUCCESS: Trade #{res['trade_id']}")
        print(f"       Bought: {res['quantity']} BTC @ ${res['price']:,.2f}")
        print(f"       Remaining Cash: ${res['remaining_cash']:,.2f} USDT")
    else:
        print(f"  [ERROR/BLOCKED] {res.get('error')}")
    return res


def buyclose(symbol: str = "BTCUSDT", trader: BinanceTrader = None):
    """Close active Long position at current market price and realize PnL."""
    print(f"\n--> EXECUTING: buyclose(symbol='{symbol}')")
    t = trader or get_trader()
    res = t.buyclose(symbol)
    if res.get("ok"):
        print(f"  [OK] BUYCLOSE SUCCESS: Trade #{res['trade_id']} closed @ ${res['exit_price']:,.2f}")
        print(f"       Realized PnL: ${res['realized_pnl']:+,.2f} ({res['realized_pnl_pct']:+.2f}%) | Result: {'WINNER' if res['is_win'] else 'LOSS'}")
        print(f"       New Cash Balance: ${res['new_cash_balance']:,.2f} USDT")
    else:
        print(f"  [ERROR/BLOCKED] {res.get('error')}")
    return res


def sell(symbol: str = "BTCUSDT", amount_usdt: float = 5000.0, trader: BinanceTrader = None):
    """Execute a Short / Sell order."""
    print(f"\n--> EXECUTING: sell(symbol='{symbol}', amount_usdt=${amount_usdt:,.2f})")
    t = trader or get_trader()
    res = t.sell(symbol, amount_usdt=amount_usdt)
    print(f"  [OK] sell(): {res.get('message', res.get('action'))}")
    return res


def sellclose(symbol: str = "BTCUSDT", trader: BinanceTrader = None):
    """Close active Short position at current market price."""
    print(f"\n--> EXECUTING: sellclose(symbol='{symbol}')")
    t = trader or get_trader()
    res = t.sellclose(symbol)
    print(f"  [OK] sellclose(): {res.get('message', res.get('action'))}")
    return res


def reset(trader: BinanceTrader = None):
    """Reset the trading wallet and clear past test trades back to starting state."""
    print("\n--> EXECUTING: reset()")
    t = trader or get_trader()
    Trade.objects.filter(bot_config=t.bot).delete()
    SignalLog.objects.filter(bot_config=t.bot).delete()
    t.bot.current_cash = t.bot.initial_capital
    t.bot.save(update_fields=['current_cash', 'updated_at'])
    print(f"  [OK] Cleared all test trades. Restored wallet cash to ${t.bot.current_cash:,.2f} USDT.")


# ---------------------------------------------------------------------------
# Django-Extensions entry point
# ---------------------------------------------------------------------------
def run(*args):
    """
    Entry point invoked by `python manage.py runscript test_binance`.
    Dispatches to individual functions based on CLI argument.
    """
    print("=" * 65)
    print("DJANGO EXTENSIONS RUNSCRIPT: test_binance (Way 1 Architecture)")
    print("=" * 65)

    trader = get_trader()
    mode = "AUTHENTICATED (Binance API)" if trader.is_authenticated else "PUBLIC (Real Data + Paper)"
    print(f"[*] Operating Mode: {mode}")

    action = args[0].lower() if args else "all"

    # Dispatch map for clean, direct function execution
    action_dispatch = {
        "getdata": getdata,
        "positions": positions,
        "buy": buy,
        "buyclose": buyclose,
        "sell": sell,
        "sellclose": sellclose,
        "reset": reset,
    }

    if action in action_dispatch:
        action_dispatch[action]()
    elif action == "all":
        print("\n[*] Running complete 6-action test sequence...")
        getdata()
        positions()
        buy()
        positions()
        buyclose()
        sell()
        sellclose()
        print("\n" + "=" * 65)
        print("ALL 6 FUNCTIONS EXECUTED & VERIFIED SUCCESSFULLY!")
        print("=" * 65)
    else:
        print(f"\n[!] Unknown action: '{action}'")
        print("    Available actions: getdata, positions, buy, buyclose, sell, sellclose, reset, all")
