"""
Script 5: SELL
Evaluates/executes a SELL signal (Short position or spot exit).
Usage: .\venv\Scripts\python.exe test_5_sell.py
"""

import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stockgame.settings')
django.setup()

from trading.services.binance_trader import BinanceTrader


def main():
    print("=" * 65)
    print("RUNNING FUNCTION: sell()")
    print("=" * 65)

    trader = BinanceTrader()
    symbol = "BTCUSDT"

    print(f"[*] Executing SELL signal evaluation for {symbol}...")
    res = trader.sell(symbol=symbol)

    print(f"\n[OUTPUT] Action: {res.get('action')}")
    if res.get("ok"):
        print(f"  Result:  {res.get('message', 'Executed')}")
        if res.get("trade_id"):
            print(f"  Trade Closed: #{res['trade_id']} | Realized PnL: ${res['realized_pnl']:+,.2f}")
    else:
        print(f"  Blocked: {res.get('error')}")

    print("=" * 65)


if __name__ == "__main__":
    main()
