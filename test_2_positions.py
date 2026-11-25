"""
Script 2: POSITIONS
Checks current wallet balances, active open trade status, and floating PnL.
Usage: .\venv\Scripts\python.exe test_2_positions.py
"""

import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stockgame.settings')
django.setup()

from trading.services.binance_trader import BinanceTrader


def main():
    print("=" * 65)
    print("RUNNING FUNCTION: positions()")
    print("=" * 65)

    trader = BinanceTrader()
    symbol = "BTCUSDT"

    print(f"[*] Checking positions and balances for {symbol}...")
    pos = trader.positions(symbol=symbol)

    print(f"\n[WALLET STATUS]")
    print(f"  Operating Mode:    {pos['mode']}")
    print(f"  Live Market Price: ${pos['live_price']:,.2f}")
    print(f"  Available Cash:    ${pos['wallet_cash']:,.2f} USDT")

    if pos["real_usdt_balance"] is not None:
        print(f"  Real Binance USDT: ${pos['real_usdt_balance']:,.2f}")
    if pos["real_asset_balance"] is not None:
        print(f"  Real Binance BTC:  {pos['real_asset_balance']:.6f} BTC")

    print(f"\n[OPEN POSITION STATUS]")
    if pos["has_open_position"]:
        op = pos["open_position"]
        print(f"  Status:          HOLDING OPEN LONG")
        print(f"  Trade ID:        #{op['trade_id']}")
        print(f"  Entry Time:      {op['entry_time']}")
        print(f"  Entry Price:     ${op['entry_price']:,.2f}")
        print(f"  Holding Qty:     {op['quantity']} {symbol}")
        print(f"  Invested Cost:   ${op['invested_cost']:,.2f}")
        print(f"  Current Value:   ${op['current_value']:,.2f}")
        print(f"  Unrealized PnL:  ${op['unrealized_pnl']:+,.2f} ({op['unrealized_pnl_pct']:+.2f}%)")
    else:
        print(f"  Status:          NO OPEN POSITION (100% Cash)")

    print("=" * 65)


if __name__ == "__main__":
    main()
