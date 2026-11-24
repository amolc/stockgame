from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from trading.models import BotConfig, Trade, SignalLog
from trading.services.binance_service import BinanceService


class Command(BaseCommand):
    help = "Manually execute and inspect individual trading actions (BUY, CLOSE, STATUS, RESET)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--action',
            type=str,
            required=True,
            choices=['buy', 'close', 'status', 'reset'],
            help="The manual action to execute: buy, close, status, or reset"
        )
        parser.add_argument(
            '--amount',
            type=float,
            default=None,
            help="Amount in USDT to invest on BUY (default: uses all available cash in BotConfig)"
        )

    def handle(self, *args, **options):
        action = options['action'].lower()
        amount = options.get('amount')

        bot = BotConfig.objects.filter(is_active=True).first()
        if not bot:
            self.stdout.write(self.style.ERROR("[!] No active BotConfig found. Creating default bot..."))
            bot = BotConfig.objects.create(
                name="BTCUSDT Default EMA Bot",
                symbol="BTCUSDT",
                interval="15m",
                initial_capital=Decimal("10000.00"),
                current_cash=Decimal("10000.00"),
                is_active=True
            )

        binance = BinanceService()
        current_price = Decimal(str(binance.get_ticker_price(bot.symbol)))

        self.stdout.write(self.style.NOTICE("=" * 65))
        self.stdout.write(self.style.NOTICE(f"MANUAL ACTION INSPECTION TOOL: [{action.upper()}]"))
        self.stdout.write(self.style.NOTICE(f"Bot: {bot.name} | Symbol: {bot.symbol} | Live Market Price: ${current_price:,.2f}"))
        self.stdout.write(self.style.NOTICE("=" * 65))

        # ----------------------------------------------------
        # ACTION: STATUS
        # ----------------------------------------------------
        if action == 'status':
            open_trade = Trade.objects.filter(bot_config=bot, status='OPEN').first()
            self.stdout.write(f"\n[WALLET STATE]")
            self.stdout.write(f"  Available Cash:   ${bot.current_cash:,.2f} USDT")
            self.stdout.write(f"  Trading Mode:     {bot.mode}")

            if open_trade:
                gross_val = open_trade.quantity * current_price
                unrealized_pnl = gross_val - open_trade.cost
                unrealized_pnl_pct = (unrealized_pnl / open_trade.cost) * Decimal("100.0")
                self.stdout.write(self.style.SUCCESS(f"\n[ACTIVE POSITION: OPEN]"))
                self.stdout.write(f"  Entry Time:       {open_trade.entry_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                self.stdout.write(f"  Entry Price:      ${open_trade.entry_price:,.2f}")
                self.stdout.write(f"  Holding Quantity: {open_trade.quantity:.6f} {bot.symbol}")
                self.stdout.write(f"  Invested Cost:    ${open_trade.cost:,.2f}")
                self.stdout.write(f"  Current Value:    ${gross_val:,.2f}")
                pnl_color = self.style.SUCCESS if unrealized_pnl >= 0 else self.style.ERROR
                self.stdout.write(pnl_color(f"  Unrealized PnL:   ${unrealized_pnl:+,.2f} ({unrealized_pnl_pct:+.2f}%)"))
            else:
                self.stdout.write(self.style.WARNING("\n[ACTIVE POSITION: NONE (100% Cash)]"))

        # ----------------------------------------------------
        # ACTION: BUY
        # ----------------------------------------------------
        elif action == 'buy':
            open_trade = Trade.objects.filter(bot_config=bot, status='OPEN').first()
            if open_trade:
                self.stdout.write(self.style.ERROR(
                    f"\n[BLOCKED] Cannot BUY: Bot already holds an active position (Trade #{open_trade.id})!\n"
                    f"Entry Price: ${open_trade.entry_price:,.2f} | Quantity: {open_trade.quantity:.6f}"
                ))
                self.stdout.write(self.style.NOTICE("Tip: Run '--action close' first to exit this trade before opening another."))
                return

            spend_amount = Decimal(str(amount)) if amount else bot.current_cash
            if spend_amount <= Decimal("10.00"):
                self.stdout.write(self.style.ERROR(
                    f"\n[BLOCKED] Insufficient funds: spend amount (${spend_amount:,.2f}) is below minimum notional ($10.00 USDT)!"
                ))
                return

            if spend_amount > bot.current_cash:
                self.stdout.write(self.style.ERROR(
                    f"\n[BLOCKED] Insufficient cash: requested ${spend_amount:,.2f}, but wallet only has ${bot.current_cash:,.2f} USDT!"
                ))
                return

            # Fee & Lot Precision calculation
            fee = spend_amount * bot.fee_pct
            investable = spend_amount - fee
            # Binance precision: 5 decimal places for BTC
            raw_qty = investable / current_price
            quantity = Decimal(str(round(float(raw_qty), 5)))

            self.stdout.write(f"\n[EXECUTING BUY ORDER]")
            self.stdout.write(f"  1. Capital to Allocate: ${spend_amount:,.2f} USDT")
            self.stdout.write(f"  2. Binance Taker Fee:   ${fee:,.2f} ({bot.fee_pct * 100:.2f}%)")
            self.stdout.write(f"  3. Net Capital:         ${investable:,.2f} USDT")
            self.stdout.write(f"  4. Execution Price:     ${current_price:,.2f}")
            self.stdout.write(f"  5. Purchased Quantity:  {quantity} {bot.symbol} (Rounded to LOT_SIZE step 0.00001)")

            trade = Trade.objects.create(
                bot_config=bot,
                symbol=bot.symbol,
                status='OPEN',
                entry_time=timezone.now(),
                entry_price=current_price,
                quantity=quantity,
                cost=spend_amount,
                notes="Manual BUY Trigger via CLI"
            )

            bot.current_cash -= spend_amount
            bot.save(update_fields=['current_cash', 'updated_at'])

            SignalLog.objects.create(
                bot_config=bot,
                timestamp=timezone.now(),
                symbol=bot.symbol,
                signal_type='BUY',
                price=current_price,
                fast_ema=Decimal("0"),
                slow_ema=Decimal("0"),
                executed=True,
                message=f"Manual BUY executed: {quantity} {bot.symbol} @ ${current_price:,.2f}"
            )

            self.stdout.write(self.style.SUCCESS(f"\n[SUCCESS] Position #{trade.id} is now OPEN! Remaining Cash: ${bot.current_cash:,.2f} USDT"))

        # ----------------------------------------------------
        # ACTION: CLOSE (or SQUARE-OFF)
        # ----------------------------------------------------
        elif action == 'close':
            open_trade = Trade.objects.filter(bot_config=bot, status='OPEN').first()
            if not open_trade:
                self.stdout.write(self.style.WARNING(
                    "\n[BLOCKED] Cannot CLOSE: There is NO open position to close! (You are currently in 100% Cash)."
                ))
                return

            gross_revenue = open_trade.quantity * current_price
            exit_fee = gross_revenue * bot.fee_pct
            net_revenue = gross_revenue - exit_fee
            pnl = net_revenue - open_trade.cost
            pnl_pct = (pnl / open_trade.cost) * Decimal("100.0")

            self.stdout.write(f"\n[EXECUTING CLOSE / SQUARE-OFF]")
            self.stdout.write(f"  1. Trade ID:            #{open_trade.id}")
            self.stdout.write(f"  2. Entry Price:         ${open_trade.entry_price:,.2f}")
            self.stdout.write(f"  3. Exit Price:          ${current_price:,.2f}")
            self.stdout.write(f"  4. Quantity Sold:       {open_trade.quantity} {bot.symbol}")
            self.stdout.write(f"  5. Gross Revenue:       ${gross_revenue:,.2f}")
            self.stdout.write(f"  6. Exit Fee (0.1%):     ${exit_fee:,.2f}")
            self.stdout.write(f"  7. Net Revenue:         ${net_revenue:,.2f}")

            pnl_style = self.style.SUCCESS if pnl >= 0 else self.style.ERROR
            self.stdout.write(pnl_style(f"  8. Realized Net PnL:    ${pnl:+,.2f} ({pnl_pct:+.2f}%)"))

            # Update Trade record
            open_trade.status = 'CLOSED'
            open_trade.exit_time = timezone.now()
            open_trade.exit_price = current_price
            open_trade.net_revenue = net_revenue
            open_trade.pnl = pnl
            open_trade.pnl_pct = pnl_pct
            open_trade.is_win = pnl > 0
            open_trade.notes += f" | Manual CLOSE @ ${current_price:,.2f}"
            open_trade.save()

            # Return funds to bot cash
            bot.current_cash += net_revenue
            bot.save(update_fields=['current_cash', 'updated_at'])

            SignalLog.objects.create(
                bot_config=bot,
                timestamp=timezone.now(),
                symbol=bot.symbol,
                signal_type='SELL',
                price=current_price,
                fast_ema=Decimal("0"),
                slow_ema=Decimal("0"),
                executed=True,
                message=f"Manual CLOSE: Realized PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
            )

            self.stdout.write(self.style.SUCCESS(f"\n[SUCCESS] Trade #{open_trade.id} CLOSED. New Cash Balance: ${bot.current_cash:,.2f} USDT"))

        # ----------------------------------------------------
        # ACTION: RESET
        # ----------------------------------------------------
        elif action == 'reset':
            Trade.objects.filter(bot_config=bot).delete()
            SignalLog.objects.filter(bot_config=bot).delete()
            bot.current_cash = bot.initial_capital
            bot.save(update_fields=['current_cash', 'updated_at'])
            self.stdout.write(self.style.SUCCESS(f"\n[RESET] Cleared all trades & signals. Wallet restored to ${bot.current_cash:,.2f} USDT."))
