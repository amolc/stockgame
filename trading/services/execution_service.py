from decimal import Decimal
from typing import Optional
from binance.client import Client
from django.db import transaction
from trading.models import BotConfig, Trade, SignalLog
from trading.services.binance_service import BinanceService


class ExecutionService:
    @staticmethod
    def process_signal(
        bot_config: BotConfig,
        signal_data: dict,
        binance: Optional[BinanceService] = None,
    ) -> dict:
        signal_type = signal_data.get("signal", "NONE")
        if "timestamp" not in signal_data or "price" not in signal_data:
            reason = signal_data.get("reason", "Insufficient candle data to evaluate EMA crossover.")
            return {
                "signal": signal_type,
                "executed": False,
                "message": reason,
                "price": signal_data.get("active_price", 0.0),
                "current_cash": float(bot_config.current_cash),
            }

        candle_time = signal_data["timestamp"]
        price = Decimal(str(signal_data["price"]))
        fast_ema = Decimal(str(signal_data["fast_ema"]))
        slow_ema = Decimal(str(signal_data["slow_ema"]))
        fee_pct = bot_config.fee_pct

        # Deduplicate repeated polls on the exact same closed candle
        if SignalLog.objects.filter(bot_config=bot_config, timestamp=candle_time).exists():
            return {
                "signal": signal_type,
                "executed": False,
                "duplicate": True,
                "message": f"Candle {candle_time.strftime('%Y-%m-%d %H:%M UTC')} already evaluated; waiting for next close.",
                "price": float(price),
                "current_cash": float(bot_config.current_cash),
            }

        executed = False
        message = ""
        is_live = bot_config.mode == "LIVE"
        if is_live and binance is None:
            binance = BinanceService()

        # Check existing open trade for this bot
        open_trade = Trade.objects.filter(bot_config=bot_config, status='OPEN').first()

        if signal_type == "BUY":
            configured_size = bot_config.order_amount_usdt
            spend = min(configured_size, bot_config.current_cash) if configured_size > 0 else bot_config.current_cash

            if open_trade is not None:
                message = f"BUY signal ignored: Already holding an open position from {open_trade.entry_time}"
            elif spend < Decimal("10.00"):
                message = (
                    f"BUY signal ignored: Order size (${spend:.2f}) is below minimum $10.00 USDT "
                    f"(Available cash: ${bot_config.current_cash:.2f})"
                )
            elif is_live and not binance.is_authenticated:
                message = "BUY signal failed: Bot mode is LIVE but BINANCE_API_KEY/SECRET are missing."
            else:
                fee = spend * fee_pct
                investable = spend - fee
                raw_qty = investable / price
                binance_order_id = None

                if is_live:
                    quantity = binance.round_quantity(bot_config.symbol, raw_qty)
                    if quantity <= 0:
                        message = f"BUY signal ignored: Rounded order quantity is 0 for ${spend:.2f} {bot_config.symbol}."
                    else:
                        res = binance.place_market_order(bot_config.symbol, Client.SIDE_BUY, quantity)
                        binance_order_id = res.get("orderId")
                        if res.get("executedQty") and float(res["executedQty"]) > 0:
                            quantity = Decimal(str(res["executedQty"]))
                        if res.get("cummulativeQuoteQty") and float(res["cummulativeQuoteQty"]) > 0:
                            spend = Decimal(str(res["cummulativeQuoteQty"]))
                            price = spend / quantity
                else:
                    quantity = raw_qty

                if not message:
                    mode_tag = f"Binance #{binance_order_id}" if binance_order_id else "Paper"
                    with transaction.atomic():
                        Trade.objects.create(
                            bot_config=bot_config,
                            symbol=bot_config.symbol,
                            status='OPEN',
                            entry_time=candle_time,
                            entry_price=price,
                            quantity=quantity,
                            cost=spend,
                            notes=f"Auto BUY ({mode_tag}) EMA {bot_config.fast_ema_period}>{bot_config.slow_ema_period}",
                        )
                        bot_config.current_cash -= spend
                        bot_config.save(update_fields=['current_cash', 'updated_at'])
                    executed = True
                    message = (
                        f"Executed {bot_config.mode} BUY: {quantity:.6f} {bot_config.symbol} @ ${price:,.2f} "
                        f"(Cost: ${spend:,.2f})"
                    )

        elif signal_type == "SELL":
            if open_trade is None:
                message = "SELL signal ignored: No open position currently held."
            elif is_live and not binance.is_authenticated:
                message = "SELL signal failed: Bot mode is LIVE but BINANCE_API_KEY/SECRET are missing."
            else:
                qty = open_trade.quantity
                gross_revenue = qty * price
                exit_fee = gross_revenue * fee_pct
                net_revenue = gross_revenue - exit_fee
                exit_price = price
                binance_order_id = None

                if is_live:
                    sell_qty = binance.round_quantity(bot_config.symbol, qty)
                    res = binance.place_market_order(bot_config.symbol, Client.SIDE_SELL, sell_qty)
                    binance_order_id = res.get("orderId")
                    if res.get("executedQty") and float(res["executedQty"]) > 0:
                        qty = Decimal(str(res["executedQty"]))
                    if res.get("cummulativeQuoteQty") and float(res["cummulativeQuoteQty"]) > 0:
                        net_revenue = Decimal(str(res["cummulativeQuoteQty"]))
                        exit_price = net_revenue / qty

                pnl = net_revenue - open_trade.cost
                pnl_pct = (pnl / open_trade.cost) * Decimal("100.0") if open_trade.cost else Decimal("0.00")
                mode_tag = f"Binance #{binance_order_id}" if binance_order_id else "Paper"

                with transaction.atomic():
                    open_trade.status = 'CLOSED'
                    open_trade.exit_time = candle_time
                    open_trade.exit_price = exit_price
                    open_trade.net_revenue = net_revenue
                    open_trade.pnl = pnl
                    open_trade.pnl_pct = pnl_pct
                    open_trade.is_win = pnl > 0
                    open_trade.notes += f" | Auto SELL ({mode_tag} @ ${exit_price:,.2f})"
                    open_trade.save()

                    bot_config.current_cash += net_revenue
                    bot_config.save(update_fields=['current_cash', 'updated_at'])

                executed = True
                message = (
                    f"Executed {bot_config.mode} SELL: Closed @ ${exit_price:,.2f} | "
                    f"Net PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
                )

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
