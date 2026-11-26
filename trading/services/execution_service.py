from decimal import Decimal
from typing import Optional
from binance.client import Client
from django.db import transaction
from trading.models import BotConfig, Trade, SignalLog
from trading.services.binance_service import BinanceService


class ExecutionService:
    """
    Executes EMA-crossover signals as long/short futures positions.

    - BUY  (golden cross): close any SHORT, then open LONG.
    - SELL (death cross) : close any LONG, then open SHORT.
    Repeated polls on the same closed candle are ignored.
    """

    @staticmethod
    def _open_position(
        bot_config: BotConfig,
        binance: Optional[BinanceService],
        side: str,
        price: Decimal,
        quantity: Decimal,
        spend: Decimal,
        candle_time,
    ) -> tuple[bool, str]:
        is_live = bot_config.mode == "LIVE"
        order_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        binance_order_id = None

        if is_live:
            if binance is None:
                binance = BinanceService()
            if not binance.is_authenticated:
                return False, f"{side} signal failed: Bot mode is LIVE but BINANCE_API_KEY/SECRET are missing."

            binance.set_leverage(bot_config.symbol)
            quantity = binance.round_quantity(bot_config.symbol, quantity)
            if quantity <= 0:
                return False, f"{side} signal ignored: Rounded order quantity is 0 for ${spend:.2f} {bot_config.symbol}."

            res = binance.place_market_order(bot_config.symbol, order_side, quantity)
            binance_order_id = res.get("orderId")
            if res.get("executedQty") and float(res["executedQty"]) > 0:
                quantity = Decimal(str(res["executedQty"]))
            if res.get("cummulativeQuoteQty") and float(res["cummulativeQuoteQty"]) > 0:
                spend = Decimal(str(res["cummulativeQuoteQty"]))
                price = spend / quantity if quantity else price

        mode_tag = f"Binance #{binance_order_id}" if binance_order_id else "Paper"
        with transaction.atomic():
            Trade.objects.create(
                bot_config=bot_config,
                symbol=bot_config.symbol,
                status='OPEN',
                side=side,
                entry_time=candle_time,
                entry_price=price,
                quantity=quantity,
                cost=spend,
                notes=(
                    f"Auto {side} BUY ({mode_tag}) EMA "
                    f"{bot_config.fast_ema_period}>{bot_config.slow_ema_period}"
                ),
            )
            bot_config.current_cash -= spend
            bot_config.save(update_fields=['current_cash', 'updated_at'])

        return True, (
            f"Executed {bot_config.mode} {side} OPEN: {quantity:.6f} {bot_config.symbol} "
            f"@ ${price:,.2f} (Cost: ${spend:,.2f})"
        )

    @staticmethod
    def _close_position(
        bot_config: BotConfig,
        open_trade: Trade,
        binance: Optional[BinanceService],
        candle_time,
        price: Decimal,
    ) -> tuple[bool, str]:
        is_live = bot_config.mode == "LIVE"
        fee_pct = bot_config.fee_pct
        qty = open_trade.quantity
        entry_price = open_trade.entry_price
        cost = open_trade.cost
        is_long = open_trade.side == "LONG"

        exit_price = price
        binance_order_id = None

        if is_live:
            if binance is None:
                binance = BinanceService()
            if not binance.is_authenticated:
                return False, "SELL signal failed: Bot mode is LIVE but BINANCE_API_KEY/SECRET are missing."

            close_side = Client.SIDE_SELL if is_long else Client.SIDE_BUY
            close_qty = binance.round_quantity(bot_config.symbol, qty)
            res = binance.place_market_order(
                bot_config.symbol, close_side, close_qty, reduce_only=True
            )
            binance_order_id = res.get("orderId")
            if res.get("executedQty") and float(res["executedQty"]) > 0:
                qty = Decimal(str(res["executedQty"]))
            if res.get("cummulativeQuoteQty") and float(res["cummulativeQuoteQty"]) > 0:
                quote_qty = Decimal(str(res["cummulativeQuoteQty"]))
                exit_price = quote_qty / qty if qty else exit_price

        entry_fee = cost * fee_pct
        exit_fee = qty * exit_price * fee_pct
        if is_long:
            net_revenue = qty * exit_price - exit_fee
            pnl = net_revenue - cost
        else:
            pnl = (entry_price - exit_price) * qty - entry_fee - exit_fee
            net_revenue = cost + pnl

        pnl_pct = (pnl / cost) * Decimal("100.0") if cost else Decimal("0.00")
        mode_tag = f"Binance #{binance_order_id}" if binance_order_id else "Paper"

        with transaction.atomic():
            open_trade.status = 'CLOSED'
            open_trade.exit_time = candle_time
            open_trade.exit_price = exit_price
            open_trade.net_revenue = net_revenue
            open_trade.pnl = pnl
            open_trade.pnl_pct = pnl_pct
            open_trade.is_win = pnl > 0
            open_trade.notes += f" | Auto {open_trade.side} CLOSE ({mode_tag} @ ${exit_price:,.2f})"
            open_trade.save()

            bot_config.current_cash += net_revenue
            bot_config.save(update_fields=['current_cash', 'updated_at'])

        return True, (
            f"Executed {bot_config.mode} {open_trade.side} CLOSE @ ${exit_price:,.2f} | "
            f"Net PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
        )

    @classmethod
    def process_signal(
        cls,
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

        is_live = bot_config.mode == "LIVE"
        if is_live and binance is None:
            binance = BinanceService()

        open_trade = Trade.objects.filter(bot_config=bot_config, status='OPEN').first()

        executed = False
        messages = []

        if signal_type in ("BUY", "SELL"):
            target_side = "LONG" if signal_type == "BUY" else "SHORT"

            if open_trade is not None and open_trade.side == target_side:
                messages.append(
                    f"{signal_type} signal ignored: Already holding an open {target_side} "
                    f"position from {open_trade.entry_time}."
                )
            else:
                if open_trade is not None:
                    closed, close_msg = cls._close_position(
                        bot_config, open_trade, binance, candle_time, price
                    )
                    messages.append(close_msg)
                    executed = closed
                    if not closed:
                        messages.append(f"{signal_type} aborted: Could not close existing {open_trade.side}.")
                        target_side = None

                if target_side is not None:
                    spend = (
                        min(bot_config.order_amount_usdt, bot_config.current_cash)
                        if bot_config.order_amount_usdt > 0
                        else bot_config.current_cash
                    )
                    if spend < Decimal("10.00"):
                        messages.append(
                            f"{signal_type} signal ignored: Order size (${spend:.2f}) is below minimum "
                            f"$10.00 USDT (Available cash: ${bot_config.current_cash:.2f})"
                        )
                    elif is_live and not binance.is_authenticated:
                        messages.append(
                            f"{signal_type} signal failed: Bot mode is LIVE but BINANCE_API_KEY/SECRET are missing."
                        )
                    else:
                        fee = spend * fee_pct
                        investable = spend - fee
                        raw_qty = investable / price
                        opened, open_msg = cls._open_position(
                            bot_config, binance, target_side, price, raw_qty, spend, candle_time
                        )
                        messages.append(open_msg)
                        executed = executed or opened
        else:
            messages.append("No crossover signal on latest candle. Holding state.")

        message = " | ".join(m for m in messages if m)

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
            message=message,
        )

        return {
            "signal": signal_type,
            "executed": executed,
            "message": message,
            "price": float(price),
            "current_cash": float(bot_config.current_cash),
        }
