"""
Script 3: BUY
Executes a BUY order to open a Long position.
Checks lot-size precision, min notional, and updates the database.
Usage: .\venv\Scripts\python.exe test_3_buy.py
"""

import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stockgame.settings')
django.setup()

from trading.services.binance_trader import BinanceTrader


def main():
    print("=" * 65)
    print("RUNNING FUNCTION: buy()")
    print("=" * 65)

    trader = BinanceTrader()
    symbol = "BTCUSDT"
    amount = 5000.0  # Allocate $5,000 USDT to buy BTC

    print(f"[*] Attempting to BUY ${amount:,.2f} USDT worth of {symbol}...")
    res = trader.buy(symbol=symbol, amount_usdt=amount)

    if res["ok"]:
        print("\n[+] SUCCESS: BUY Order Executed!")
        print(f"  Trade ID:        #{res['trade_id']}")
        print(f"  Execution Price: ${res['price']:,.2f}")
        print(f"  Quantity Bought: {res['quantity']} {symbol}")
        print(f"  Invested Cost:   ${res['cost']:,.2f} USDT")
        print(f"  Remaining Cash:  ${res['remaining_cash']:,.2f} USDT")
        if res.get("binance_order_id"):
            print(f"  Binance Order ID: {res['binance_order_id']}")
    else:
        print(f"\n[-] BLOCKED: {res['error']}")

    print("=" * 65)


if __name__ == "__main__":
    main()
