import logging
from decimal import Decimal
from typing import Optional
from binance.client import Client
from django.db import transaction
from trading.models import BotConfig, Trade, SignalLog
from trading.services.binance_service import BinanceService

logger = logging.getLogger(__name__)


class ExecutionService:
    """
    Executes EMA-crossover signals as long/short futures positions.

    - BUY  (golden cross): close any SHORT, then open LONG.
    - SELL (death cross) : close any LONG, then open SHORT.
    Repeated polls on the same closed candle are ignored.
    """

    @staticmethod
    def _is_binance_backed(trade: Trade) -> bool:
        """True when the open trade was actually placed on Binance (has an order id in notes)."""
        notes = trade.notes or ""
        return "Binance #" in notes or "Demo #" in notes

    @staticmethod
    def _is_demo_mirrored(trade: Trade) -> bool:
        """True when the open trade has a mirrored order on the Demo/Testnet account."""
        return "Demo #" in (trade.notes or "")

    @classmethod
    def sync_open_position_to_binance(
        cls,
        bot_config: BotConfig,
        binance: Optional[BinanceService] = None,
        price: Optional[Decimal] = None,
    ) -> tuple[bool, str]:
        """
        Adopt an existing local OPEN trade onto Binance when a bot is switched to LIVE.

        Paper trades only exist in the database. Without this step, switching a bot
        to LIVE leaves a phantom position that blocks new entries and cannot be
        closed with a reduce-only order. This places a matching market order and
        rewrites the trade with the real exchange fill details.
        """
        if bot_config.mode != "LIVE":
            return True, ""

        open_trade = Trade.objects.filter(bot_config=bot_config, status='OPEN').first()
        if open_trade is None or cls._is_binance_backed(open_trade):
            return True, ""

        if binance is None:
            binance = BinanceService()
        if not binance.is_authenticated:
            return False, (
                f"{open_trade.side} position sync failed: Bot mode is LIVE but "
                "BINANCE_API_KEY/SECRET are missing."
            )

        side = Client.SIDE_BUY if open_trade.side == "LONG" else Client.SIDE_SELL
        binance.set_leverage(bot_config.symbol)
        quantity = binance.round_quantity(bot_config.symbol, open_trade.quantity)
        if quantity <= 0:
            return False, (
                f"{open_trade.side} position sync ignored: Rounded order quantity is 0 "
                f"for Trade #{open_trade.id}."
            )

        try:
            res = binance.place_market_order(bot_config.symbol, side, quantity)
        except Exception as exc:
            logger.exception("Failed to sync Trade #%s to Binance", open_trade.id)
            return False, f"{open_trade.side} position sync to Binance failed: {exc}"

        order_id = res.get("orderId")
        order_ref = order_id if order_id is not None else "unknown"
        old_cost = open_trade.cost
        new_cost = old_cost

        if res.get("executedQty") and float(res["executedQty"]) > 0:
            quantity = Decimal(str(res["executedQty"]))
        if res.get("cummulativeQuoteQty") and float(res["cummulativeQuoteQty"]) > 0:
            new_cost = Decimal(str(res["cummulativeQuoteQty"]))
        fill_price = new_cost / quantity if quantity else (price or open_trade.entry_price)

        with transaction.atomic():
            open_trade.quantity = quantity
            open_trade.cost = new_cost
            open_trade.entry_price = fill_price
            open_trade.notes += f" | Synced to Binance #{order_ref} @ ${fill_price:,.2f}"
            open_trade.save()

            # The paper entry deducted old_cost from cash; reconcile to the real fill.
            bot_config.current_cash += old_cost - new_cost
            bot_config.save(update_fields=['current_cash', 'updated_at'])

        return True, (
            f"Synced existing {open_trade.side} position (Trade #{open_trade.id}) to "
            f"Binance #{order_ref}: {quantity} {bot_config.symbol} @ ${fill_price:,.2f}"
        )

    @classmethod
    def sync_open_position_to_demo(
        cls,
        bot_config: BotConfig,
        binance: Optional[BinanceService] = None,
        price: Optional[Decimal] = None,
    ) -> tuple[bool, str]:
        """
        Mirror an existing local PAPER position onto the Demo/Testnet account.

        PAPER bots keep their own simulated accounting. When mirroring is enabled,
        any open paper trade that was created before mirroring was switched on is
        copied to the demo account once, so subsequent closes are reduce-only safe.
        """
        if bot_config.mode != "PAPER" or not getattr(bot_config, "mirror_to_demo", False):
            return True, ""
        if binance is None or not binance.is_authenticated or not binance.is_demo:
            return True, ""

        open_trade = Trade.objects.filter(bot_config=bot_config, status='OPEN').first()
        if open_trade is None or cls._is_binance_backed(open_trade):
            return True, ""

        side = Client.SIDE_BUY if open_trade.side == "LONG" else Client.SIDE_SELL
        binance.set_leverage(bot_config.symbol)
        quantity = binance.round_quantity(bot_config.symbol, open_trade.quantity)
        if quantity <= 0:
            return False, (
                f"{open_trade.side} demo mirror ignored: Rounded order quantity is 0 "
                f"for Trade #{open_trade.id}."
            )

        try:
            res = binance.place_market_order(bot_config.symbol, side, quantity)
        except Exception as exc:
            logger.exception("Failed to mirror Trade #%s to demo", open_trade.id)
            return False, f"{open_trade.side} demo mirror failed: {exc}"

        order_id = res.get("orderId")
        order_ref = order_id if order_id is not None else "unknown"

        open_trade.notes += f" | Mirrored to Demo #{order_ref}"
        open_trade.save(update_fields=["notes"])

        return True, (
            f"Mirrored existing {open_trade.side} position (Trade #{open_trade.id}) to "
            f"Demo #{order_ref}: {quantity} {bot_config.symbol}"
        )

    @staticmethod
    def _open_position(
        bot_config: BotConfig,
        binance: Optional[BinanceService],
        side: str,
        price: Decimal,
        quantity: Decimal,
        spend: Decimal,
        candle_time,
        mirror: bool = False,
    ) -> tuple[bool, str]:
        is_live = bot_config.mode == "LIVE"
        order_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        binance_order_id = None
        demo_order_id = None
        mirror_error = ""

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

        if mirror and binance is not None and binance.is_authenticated and binance.is_demo:
            binance.set_leverage(bot_config.symbol)
            mirror_qty = binance.round_quantity(bot_config.symbol, quantity)
            if mirror_qty <= 0:
                mirror_error = f"Demo mirror skipped: rounded quantity is 0 for {bot_config.symbol}."
            else:
                try:
                    res = binance.place_market_order(bot_config.symbol, order_side, mirror_qty)
                    demo_order_id = res.get("orderId")
                except Exception as exc:
                    logger.exception("Failed to mirror %s %s open to demo", bot_config.symbol, side)
                    mirror_error = f"Demo mirror failed: {exc}"

        if binance_order_id:
            mode_tag = f"Binance #{binance_order_id}"
        elif demo_order_id:
            mode_tag = f"Paper | Demo #{demo_order_id}"
        else:
            mode_tag = "Paper"
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
            + (f" | {mirror_error}" if mirror_error else "")
        )

    @staticmethod
    def _close_position(
        bot_config: BotConfig,
        open_trade: Trade,
        binance: Optional[BinanceService],
        candle_time,
        price: Decimal,
        mirror: bool = False,
    ) -> tuple[bool, str]:
        is_live = bot_config.mode == "LIVE"
        fee_pct = bot_config.fee_pct
        qty = open_trade.quantity
        entry_price = open_trade.entry_price
        cost = open_trade.cost
        is_long = open_trade.side == "LONG"

        exit_price = price
        binance_order_id = None
        demo_order_id = None
        mirror_error = ""

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

        close_side = Client.SIDE_SELL if is_long else Client.SIDE_BUY
        if (
            mirror
            and ExecutionService._is_demo_mirrored(open_trade)
            and binance is not None
            and binance.is_authenticated
            and binance.is_demo
        ):
            close_qty = binance.round_quantity(bot_config.symbol, qty)
            if close_qty <= 0:
                mirror_error = f"Demo mirror skipped: rounded quantity is 0 for {bot_config.symbol}."
            else:
                try:
                    res = binance.place_market_order(
                        bot_config.symbol, close_side, close_qty, reduce_only=True
                    )
                    demo_order_id = res.get("orderId")
                except Exception as exc:
                    logger.exception("Failed to mirror %s close to demo", bot_config.symbol)
                    mirror_error = f"Demo mirror failed: {exc}"

        entry_fee = cost * fee_pct
        exit_fee = qty * exit_price * fee_pct
        if is_long:
            net_revenue = qty * exit_price - exit_fee
            pnl = net_revenue - cost
        else:
            pnl = (entry_price - exit_price) * qty - entry_fee - exit_fee
            net_revenue = cost + pnl

        pnl_pct = (pnl / cost) * Decimal("100.0") if cost else Decimal("0.00")
        if binance_order_id:
            mode_tag = f"Binance #{binance_order_id}"
        elif demo_order_id:
            mode_tag = f"Paper | Demo #{demo_order_id}"
        else:
            mode_tag = "Paper"

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
            + (f" | {mirror_error}" if mirror_error else "")
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

        is_live = bot_config.mode == "LIVE"
        if is_live and binance is None:
            binance = BinanceService()

        mirror = (
            not is_live
            and getattr(bot_config, "mirror_to_demo", False)
            and binance is not None
            and binance.is_authenticated
            and binance.is_demo
        )

        # If the bot was switched to LIVE with a paper position still open, mirror
        # it onto Binance now so the local and exchange state stay in sync. PAPER
        # bots mirror any existing paper position to the demo account instead.
        sync_message = ""
        if is_live:
            synced, sync_message = cls.sync_open_position_to_binance(
                bot_config, binance=binance, price=price
            )
            if not synced:
                logger.error(sync_message)
                return {
                    "signal": signal_type,
                    "executed": False,
                    "message": sync_message,
                    "price": float(price),
                    "current_cash": float(bot_config.current_cash),
                }
        elif mirror:
            synced, sync_message = cls.sync_open_position_to_demo(
                bot_config, binance=binance, price=price
            )
            if not synced:
                logger.warning(sync_message)

        # Deduplicate repeated polls on the exact same closed candle
        if SignalLog.objects.filter(bot_config=bot_config, timestamp=candle_time).exists():
            duplicate_message = (
                f"Candle {candle_time.strftime('%Y-%m-%d %H:%M UTC')} already evaluated; "
                "waiting for next close."
            )
            return {
                "signal": signal_type,
                "executed": False,
                "duplicate": True,
                "message": " | ".join(m for m in (sync_message, duplicate_message) if m),
                "price": float(price),
                "current_cash": float(bot_config.current_cash),
            }

        open_trade = Trade.objects.filter(bot_config=bot_config, status='OPEN').first()

        executed = False
        messages = [sync_message] if sync_message else []

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
                        bot_config, open_trade, binance, candle_time, price, mirror=mirror
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
                            bot_config, binance, target_side, price, raw_qty, spend, candle_time,
                            mirror=mirror,
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
