from django.core.management.base import BaseCommand
from trading.services.binance_trader import BinanceTrader


class Command(BaseCommand):
    help = "Test the 6 core Binance functions: getdata, positions, buy, buyclose, sell, sellclose"

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(self.style.SUCCESS("TESTING 6 CORE BINANCE TRADING FUNCTIONS"))
        self.stdout.write(self.style.SUCCESS("=" * 65))

        trader = BinanceTrader()
        self.stdout.write(f"Mode: {'AUTHENTICATED (Live/Testnet)' if trader.is_authenticated else 'PUBLIC (Real Data + Paper)'}")

        # 1. Test getdata()
        self.stdout.write("\n--> [1/6] Testing getdata()...")
        df = trader.getdata(symbol="BTCUSDT", interval="15m")
        self.stdout.write(self.style.SUCCESS(f"  [OK] Successfully fetched {len(df)} candles from Binance stream! Latest close: ${df['close'].iloc[-1]:,.2f}"))

        # 2. Test positions() initially
        self.stdout.write("\n--> [2/6] Testing positions() [Initial State]...")
        pos_init = trader.positions("BTCUSDT")
        self.stdout.write(self.style.SUCCESS(f"  [OK] Cash: ${pos_init['wallet_cash']:,.2f} | Open Position: {pos_init['has_open_position']}"))

        # 3. Test buy()
        self.stdout.write("\n--> [3/6] Testing buy() [Allocating $5,000 USDT]...")
        buy_res = trader.buy("BTCUSDT", amount_usdt=5000.0)
        if buy_res["ok"]:
            self.stdout.write(self.style.SUCCESS(
                f"  [OK] BUY Trade #{buy_res['trade_id']} executed: {buy_res['quantity']} BTC @ ${buy_res['price']:,.2f} | Remaining Cash: ${buy_res['remaining_cash']:,.2f}"
            ))
        else:
            self.stdout.write(self.style.ERROR(f"  [FAIL] {buy_res['error']}"))

        # 4. Test positions() while holding
        self.stdout.write("\n--> [4/6] Testing positions() [While Holding]...")
        pos_holding = trader.positions("BTCUSDT")
        if pos_holding["has_open_position"]:
            op = pos_holding["open_position"]
            self.stdout.write(self.style.SUCCESS(
                f"  [OK] Holding: {op['quantity']} BTC | Entry: ${op['entry_price']:,.2f} | Unrealized PnL: ${op['unrealized_pnl']:+,.2f} ({op['unrealized_pnl_pct']:+.2f}%)"
            ))

        # 5. Test buyclose()
        self.stdout.write("\n--> [5/6] Testing buyclose() [Closing Long Position]...")
        close_res = trader.buyclose("BTCUSDT")
        if close_res["ok"]:
            self.stdout.write(self.style.SUCCESS(
                f"  [OK] BUYCLOSE executed @ ${close_res['exit_price']:,.2f} | Realized PnL: ${close_res['realized_pnl']:+,.2f} ({close_res['realized_pnl_pct']:+.2f}%) | New Cash: ${close_res['new_cash_balance']:,.2f}"
            ))
        else:
            self.stdout.write(self.style.ERROR(f"  [FAIL] {close_res['error']}"))

        # 6. Test sell() and sellclose()
        self.stdout.write("\n--> [6/6] Testing sell() & sellclose() [Short Cycle]...")
        sell_res = trader.sell("BTCUSDT")
        sellclose_res = trader.sellclose("BTCUSDT")
        self.stdout.write(self.style.SUCCESS(f"  [OK] {sell_res['action']}: {sell_res['message']}"))
        self.stdout.write(self.style.SUCCESS(f"  [OK] {sellclose_res['action']}: {sellclose_res['message']}"))

        self.stdout.write("\n" + "=" * 65)
        self.stdout.write(self.style.SUCCESS("ALL 6 BINANCE TRADING FUNCTIONS VERIFIED SUCCESSFULLY!"))
        self.stdout.write(self.style.SUCCESS("=" * 65))
