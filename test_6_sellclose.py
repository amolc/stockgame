"""
Script 6: SELLCLOSE
Covers an active Short position back to cash.
Usage: .\venv\Scripts\python.exe test_6_sellclose.py
"""

import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stockgame.settings')
django.setup()

from trading.services.binance_trader import BinanceTrader


def main():
    print("=" * 65)
    print("RUNNING FUNCTION: sellclose()")
    print("=" * 65)

    trader = BinanceTrader()
    symbol = "BTCUSDT"

    print(f"[*] Executing SELLCLOSE (cover short) for {symbol}...")
    res = trader.sellclose(symbol=symbol)

    print(f"\n[OUTPUT] Action: {res.get('action')}")
    print(f"  Result: {res.get('message')}")
    print("=" * 65)


if __name__ == "__main__":
    main()
