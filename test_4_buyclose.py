"""
Script 4: BUYCLOSE
Closes the active Long position at live Binance market price.
Calculates fees, net revenue, and realized profit/loss.
Usage: .\venv\Scripts\python.exe test_4_buyclose.py
"""

import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stockgame.settings')
django.setup()

from trading.services.binance_trader import BinanceTrader


def main():
    print("=" * 65)
    print("RUNNING FUNCTION: buyclose()")
    print("=" * 65)

    trader = BinanceTrader()
    symbol = "BTCUSDT"

    print(f"[*] Attempting to BUYCLOSE active position in {symbol}...")
    res = trader.buyclose(symbol=symbol)

    if res["ok"]:
        print("\n[+] SUCCESS: Position CLOSED!")
        print(f"  Closed Trade:     #{res['trade_id']}")
        print(f"  Exit Market Price:${res['exit_price']:,.2f}")
        print(f"  Quantity Sold:    {res['quantity_sold']} {symbol}")
        print(f"  Net Revenue:      ${res['net_revenue']:,.2f} USDT")
        print(f"  Realized PnL:     ${res['realized_pnl']:+,.2f} ({res['realized_pnl_pct']:+.2f}%)")
        print(f"  Trade Outcome:    {'WINNER' if res['is_win'] else 'LOSS'}")
        print(f"  New Cash Balance: ${res['new_cash_balance']:,.2f} USDT")
        if res.get("binance_order_id"):
            print(f"  Binance Order ID: {res['binance_order_id']}")
    else:
        print(f"\n[-] BLOCKED: {res['error']}")

    print("=" * 65)


if __name__ == "__main__":
    main()
