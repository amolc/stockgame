from decimal import Decimal
from django.utils import timezone
from trading.models import BotConfig, Trade, SignalLog


class ExecutionService:
    @staticmethod
    def process_signal(bot_config: BotConfig, signal_data: dict) -> dict:
        signal_type = signal_data["signal"]
        candle_time = signal_data["timestamp"]
        price = Decimal(str(signal_data["price"]))
        fast_ema = Decimal(str(signal_data["fast_ema"]))
        slow_ema = Decimal(str(signal_data["slow_ema"]))
        fee_pct = bot_config.fee_pct

        executed = False
        message = ""

        # Check existing open trade for this bot
        open_trade = Trade.objects.filter(bot_config=bot_config, status='OPEN').first()

        if signal_type == "BUY":
            if open_trade is not None:
                message = f"BUY signal ignored: Already holding an open position from {open_trade.entry_time}"
            elif bot_config.current_cash <= Decimal("10.00"):
                message = f"BUY signal ignored: Insufficient cash balance (${bot_config.current_cash:.2f})"
            else:
                fee = bot_config.current_cash * fee_pct
                investable = bot_config.current_cash - fee
                quantity = investable / price
                cost = bot_config.current_cash

                Trade.objects.create(
                    bot_config=bot_config,
                    symbol=bot_config.symbol,
                    status='OPEN',
                    entry_time=candle_time,
                    entry_price=price,
                    quantity=quantity,
                    cost=cost,
                    notes=f"Auto BUY on Golden Cross (EMA {bot_config.fast_ema_period} > EMA {bot_config.slow_ema_period})"
                )

                bot_config.current_cash = Decimal("0.00")
                bot_config.save(update_fields=['current_cash', 'updated_at'])
                executed = True
                message = f"Executed BUY order: {quantity:.6f} {bot_config.symbol} @ ${price:.2f} (Cost: ${cost:.2f})"

        elif signal_type == "SELL":
            if open_trade is None:
                message = "SELL signal ignored: No open position currently held."
            else:
                gross_revenue = open_trade.quantity * price
                exit_fee = gross_revenue * fee_pct
                net_revenue = gross_revenue - exit_fee
                pnl = net_revenue - open_trade.cost
                pnl_pct = (pnl / open_trade.cost) * Decimal("100.0")

                open_trade.status = 'CLOSED'
                open_trade.exit_time = candle_time
                open_trade.exit_price = price
                open_trade.net_revenue = net_revenue
                open_trade.pnl = pnl
                open_trade.pnl_pct = pnl_pct
                open_trade.is_win = pnl > 0
                open_trade.notes += f" | Auto SELL on Death Cross (Exit: ${price:.2f})"
                open_trade.save()

                bot_config.current_cash = net_revenue
                bot_config.save(update_fields=['current_cash', 'updated_at'])
                executed = True
                message = f"Executed SELL order: Closed position @ ${price:.2f} | Net PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"

        else:
            message = "No crossover signal on latest candle. Holding state."

        # Record into SignalLog
        SignalLog.objects.create(
            bot_config=bot_config,
            timestamp=candle_time,
            symbol=bot_config.symbol,
            signal_type=signal_type,
            price=price,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
            executed=executed,
            message=message
        )

        return {
            "signal": signal_type,
            "executed": executed,
            "message": message,
            "price": float(price),
            "current_cash": float(bot_config.current_cash)
        }
