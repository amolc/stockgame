"""
Script 1: GETDATA
Pulls live candlestick data directly from Binance stream/REST API.
Usage: .\venv\Scripts\python.exe test_1_getdata.py
"""

import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stockgame.settings')
django.setup()

from trading.services.binance_trader import BinanceTrader


def main():
    print("=" * 65)
    print("RUNNING FUNCTION: getdata()")
    print("=" * 65)

    trader = BinanceTrader()
    symbol = "BTCUSDT"
    interval = "15m"

    print(f"[*] Connecting to Binance to fetch {symbol} ({interval}) candles...")
    df = trader.getdata(symbol=symbol, interval=interval, lookback="2 days ago UTC")

    print(f"\n[+] SUCCESS: Received {len(df)} candles from Binance!")
    print("\n--- LATEST 5 CANDLES ---")
    print(df.tail(5)[["open", "high", "low", "close", "volume"]])

    latest_candle = df.iloc[-1]
    print(f"\n[SUMMARY] Latest Close Price: ${latest_candle['close']:,.2f} | Volume: {latest_candle['volume']:,.4f} BTC")
    print("=" * 65)


if __name__ == "__main__":
    main()
