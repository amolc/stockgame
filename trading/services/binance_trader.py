"""
Binance Trading Interface
Implements the 6 core methods requested by the senior dev:
  1. getdata()   - Live Binance market data / stream
  2. positions() - Account balances and open position status
  3. buy()       - Open Long position
  4. buyclose()  - Close Long position
  5. sell()      - Open Short position (or Spot sell)
  6. sellclose() - Close Short position (buy back)
Supports both Authenticated Mode (Binance Testnet/Live) and Paper Mode.
"""

import os
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Any, Optional
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client
from django.utils import timezone
from trading.models import BotConfig, Trade, SignalLog

load_dotenv()


def _env_true(name: str) -> bool:
    return os.getenv(name, "false").lower() in ("true", "1", "yes")


class BinanceTrader:
    def __init__(self, bot_config: Optional[BotConfig] = None):
        # Attach BotConfig first so we can inherit its TradingAccount credentials if linked
        self.bot = bot_config or BotConfig.objects.select_related("account").filter(is_active=True).first()
        if not self.bot:
            self.bot = BotConfig.objects.create(
                name="BTCUSDT Default Bot",
                symbol="BTCUSDT",
                interval="15m",
                initial_capital=Decimal("10000.00"),
                current_cash=Decimal("10000.00"),
                is_active=True
            )

        acct = getattr(self.bot, "account", None)
        if acct is not None:
            is_paper = acct.environment == "PAPER"
            self.api_key = "" if is_paper else (acct.api_key or "")
            self.api_secret = "" if is_paper else acct.get_api_secret()
            self.demo = acct.environment == "DEMO"
            self.testnet = acct.environment == "TESTNET"
            self.market = (acct.market or "futures").lower()
            self.leverage = int(acct.leverage or 5)
        else:
            self.api_key = os.getenv("BINANCE_API_KEY", "")
            self.api_secret = os.getenv("BINANCE_API_SECRET", "")
            self.demo = _env_true("BINANCE_DEMO")
            self.testnet = _env_true("BINANCE_TESTNET") and not self.demo
            self.market = os.getenv("BINANCE_MARKET", "futures").lower()
            try:
                self.leverage = int(os.getenv("BINANCE_FUTURES_LEVERAGE", "5"))
            except ValueError:
                self.leverage = 5

        # Initialize Binance Client with resilient timeout for network latency
        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet,
            demo=self.demo,
            ping=False,
            requests_params={"timeout": 25},
        )

    @property
    def is_authenticated(self) -> bool:
        """Returns True if user has configured API Key & Secret."""
        return bool(self.api_key and self.api_secret)

    @property
    def is_futures(self) -> bool:
        """True when trading USDⓈ-M Futures (the default)."""
        return self.market == "futures"

    # -------------------------------------------------------------------------
    # 1. GETDATA: Replaces old TradingView library with direct Binance stream/REST
    # -------------------------------------------------------------------------
    def getdata(self, symbol: Optional[str] = None, interval: Optional[str] = None, lookback: str = "2 days ago UTC") -> pd.DataFrame:
        sym = symbol or self.bot.symbol
        inv = interval or self.bot.interval

        if self.is_futures:
            raw = self.client.futures_klines(symbol=sym, interval=inv, limit=1500)
        else:
            raw = self.client.get_historical_klines(symbol=sym, interval=inv, start_str=lookback)
        if not raw:
            raise ValueError(f"No kline data returned from Binance for {sym}")

        cols = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ]
        df = pd.DataFrame(raw, columns=cols)
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        
        keep_cols = ["open", "high", "low", "close", "volume"]
        df = df[keep_cols].astype(float)
        return df

    def get_current_price(self, symbol: Optional[str] = None) -> float:
        sym = symbol or self.bot.symbol
        if self.is_futures:
            ticker = self.client.futures_symbol_ticker(symbol=sym)
        else:
            ticker = self.client.get_symbol_ticker(symbol=sym)
        return float(ticker["price"])

    def _round_quantity(self, symbol: str, qty: Decimal) -> Decimal:
        """Floor a quantity to the exchange LOT_SIZE step (futures or spot)."""
        try:
            if self.is_futures:
                info = self.client.futures_exchange_info()
                for s in info.get("symbols", []):
                    if s.get("symbol") == symbol:
                        for f in s.get("filters", []):
                            if f.get("filterType") == "LOT_SIZE":
                                step = Decimal(str(f["stepSize"])).normalize()
                                return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
            else:
                info = self.client.get_symbol_info(symbol)
                if info:
                    for f in info.get("filters", []):
                        if f.get("filterType") == "LOT_SIZE":
                            step = Decimal(str(f["stepSize"])).normalize()
                            return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
        except Exception:
            pass
        return qty.quantize(Decimal("0.00001"), rounding=ROUND_DOWN)

    # -------------------------------------------------------------------------
    # 2. POSITIONS: Check balances, open trades, and current floating PnL
    # -------------------------------------------------------------------------
    def positions(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        sym = symbol or self.bot.symbol
        current_price = self.get_current_price(sym)

        # Check local DB for active open position
        open_trade = Trade.objects.filter(bot_config=self.bot, status='OPEN').first()
        
        # Real Binance wallet balances (if authenticated)
        real_usdt_balance = None
        real_asset_balance = None
        if self.is_authenticated:
            try:
                if self.is_futures:
                    for b in self.client.futures_account_balance():
                        if b.get("asset") == "USDT":
                            real_usdt_balance = float(b.get("availableBalance", b.get("balance", 0)))
                            break
                    for p in self.client.futures_position_information(symbol=sym):
                        amt = float(p.get("positionAmt", 0))
                        if amt:
                            real_asset_balance = abs(amt)
                            break
                else:
                    acc = self.client.get_account()
                    for b in acc.get("balances", []):
                        if b["asset"] == "USDT":
                            real_usdt_balance = float(b["free"])
                        elif b["asset"] == sym.replace("USDT", ""):
                            real_asset_balance = float(b["free"])
            except Exception:
                pass

        pos_info = {
            "symbol": sym,
            "mode": "AUTHENTICATED (Binance API)" if self.is_authenticated else "PAPER (Simulation)",
            "live_price": current_price,
            "wallet_cash": float(self.bot.current_cash),
            "real_usdt_balance": real_usdt_balance,
            "real_asset_balance": real_asset_balance,
            "has_open_position": open_trade is not None,
            "open_position": None
        }

        if open_trade:
            qty = float(open_trade.quantity)
            cost = float(open_trade.cost)
            current_val = qty * current_price
            if open_trade.side == "SHORT":
                floating_pnl = (float(open_trade.entry_price) - current_price) * qty
            else:
                floating_pnl = current_val - cost
            floating_pnl_pct = (floating_pnl / cost) * 100.0 if cost else 0.0

            pos_info["open_position"] = {
                "trade_id": open_trade.id,
                "side": open_trade.side,
                "entry_time": open_trade.entry_time.isoformat(),
                "entry_price": float(open_trade.entry_price),
                "quantity": qty,
                "invested_cost": cost,
                "current_value": round(current_val, 2),
                "unrealized_pnl": round(floating_pnl, 2),
                "unrealized_pnl_pct": round(floating_pnl_pct, 2)
            }

        return pos_info

    # -------------------------------------------------------------------------
    # 3. BUY: Open LONG position
    # -------------------------------------------------------------------------
    def buy(self, symbol: Optional[str] = None, amount_usdt: Optional[float] = None) -> Dict[str, Any]:
        sym = symbol or self.bot.symbol
        price = Decimal(str(self.get_current_price(sym)))
        spend = Decimal(str(amount_usdt)) if amount_usdt else self.bot.current_cash

        # Safety Check 1: Already holding?
        if Trade.objects.filter(bot_config=self.bot, status='OPEN').exists():
            return {"ok": False, "error": f"Cannot BUY: Bot already holds an open position in {sym}! Use buyclose() or sellclose() first."}

        # Safety Check 2: Minimum Notional
        if spend < Decimal("10.00"):
            return {"ok": False, "error": f"Cannot BUY: Spend amount (${spend:.2f}) is below Binance minimum $10 USDT!"}

        # Safety Check 3: Sufficient cash
        if spend > self.bot.current_cash:
            return {"ok": False, "error": f"Cannot BUY: Insufficient cash balance (${self.bot.current_cash:.2f} USDT)!"}

        fee = spend * self.bot.fee_pct
        investable = spend - fee
        raw_qty = investable / price
        quantity = self._round_quantity(sym, Decimal(str(raw_qty)))

        binance_order_id = None
        if self.is_authenticated:
            if self.is_futures:
                try:
                    self.client.futures_change_leverage(symbol=sym, leverage=self.leverage)
                except Exception:
                    pass
                res = self.client.futures_create_order(
                    symbol=sym,
                    side=Client.SIDE_BUY,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=float(quantity),
                )
            else:
                res = self.client.create_order(
                    symbol=sym,
                    side=Client.SIDE_BUY,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=float(quantity),
                )
            binance_order_id = res.get("orderId")
            if "executedQty" in res and float(res["executedQty"]) > 0:
                quantity = Decimal(str(res["executedQty"]))
            if "cummulativeQuoteQty" in res and float(res["cummulativeQuoteQty"]) > 0:
                spend = Decimal(str(res["cummulativeQuoteQty"]))
                price = spend / quantity

        # Record in Django Database
        trade = Trade.objects.create(
            bot_config=self.bot,
            symbol=sym,
            status='OPEN',
            side='LONG',
            entry_time=timezone.now(),
            entry_price=price,
            quantity=quantity,
            cost=spend,
            notes=f"LONG BUY Order {'(Binance #' + str(binance_order_id) + ')' if binance_order_id else '(Paper)'}"
        )

        self.bot.current_cash -= spend
        self.bot.save(update_fields=['current_cash', 'updated_at'])

        SignalLog.objects.create(
            bot_config=self.bot,
            timestamp=timezone.now(),
            symbol=sym,
            signal_type='BUY',
            price=price,
            fast_ema=Decimal("0"),
            slow_ema=Decimal("0"),
            executed=True,
            message=f"LONG BUY executed: {quantity} {sym} @ ${price:.2f}"
        )

        return {
            "ok": True,
            "action": "BUY",
            "side": "LONG",
            "trade_id": trade.id,
            "price": float(price),
            "quantity": float(quantity),
            "cost": float(spend),
            "remaining_cash": float(self.bot.current_cash),
            "binance_order_id": binance_order_id
        }

    # -------------------------------------------------------------------------
    # 4. BUYCLOSE: Close LONG position (Sell BTC back to USDT)
    # -------------------------------------------------------------------------
    def buyclose(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        sym = symbol or self.bot.symbol
        price = Decimal(str(self.get_current_price(sym)))

        open_trade = Trade.objects.filter(bot_config=self.bot, status='OPEN').first()
        if not open_trade:
            return {"ok": False, "error": "Cannot BUYCLOSE: No open Long position currently exists to close!"}
        if open_trade.side != "LONG":
            return {"ok": False, "error": "Cannot BUYCLOSE: Open position is SHORT. Use sellclose() instead."}

        qty = open_trade.quantity
        gross_rev = qty * price
        fee = gross_rev * self.bot.fee_pct
        net_rev = gross_rev - fee
        pnl = net_rev - open_trade.cost
        pnl_pct = (pnl / open_trade.cost) * Decimal("100.0")

        binance_order_id = None
        if self.is_authenticated:
            sell_qty = self._round_quantity(sym, qty)
            if self.is_futures:
                res = self.client.futures_create_order(
                    symbol=sym,
                    side=Client.SIDE_SELL,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=float(sell_qty),
                    reduceOnly="true",
                )
            else:
                res = self.client.create_order(
                    symbol=sym,
                    side=Client.SIDE_SELL,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=float(sell_qty),
                )
            binance_order_id = res.get("orderId")
            if "executedQty" in res and float(res["executedQty"]) > 0:
                qty = Decimal(str(res["executedQty"]))
            if "cummulativeQuoteQty" in res and float(res["cummulativeQuoteQty"]) > 0:
                net_rev = Decimal(str(res["cummulativeQuoteQty"]))
                price = net_rev / qty
                pnl = net_rev - open_trade.cost
                pnl_pct = (pnl / open_trade.cost) * Decimal("100.0")

        # Mark trade as CLOSED in Django DB
        open_trade.status = 'CLOSED'
        open_trade.exit_time = timezone.now()
        open_trade.exit_price = price
        open_trade.net_revenue = net_rev
        open_trade.pnl = pnl
        open_trade.pnl_pct = pnl_pct
        open_trade.is_win = pnl > 0
        open_trade.notes += f" | LONG BUYCLOSE @ ${price:.2f}"
        open_trade.save()

        self.bot.current_cash += net_rev
        self.bot.save(update_fields=['current_cash', 'updated_at'])

        SignalLog.objects.create(
            bot_config=self.bot,
            timestamp=timezone.now(),
            symbol=sym,
            signal_type='SELL',
            price=price,
            fast_ema=Decimal("0"),
            slow_ema=Decimal("0"),
            executed=True,
            message=f"BUYCLOSE executed: Realized PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
        )

        return {
            "ok": True,
            "action": "BUYCLOSE",
            "trade_id": open_trade.id,
            "exit_price": float(price),
            "quantity_sold": float(qty),
            "net_revenue": float(net_rev),
            "realized_pnl": float(pnl),
            "realized_pnl_pct": float(pnl_pct),
            "is_win": bool(pnl > 0),
            "new_cash_balance": float(self.bot.current_cash),
            "binance_order_id": binance_order_id
        }

    # -------------------------------------------------------------------------
    # 5. SELL: Open SHORT position (futures)
    # -------------------------------------------------------------------------
    def sell(self, symbol: Optional[str] = None, amount_usdt: Optional[float] = None) -> Dict[str, Any]:
        """Open a SHORT futures position (sells to open, not to close)."""
        sym = symbol or self.bot.symbol
        price = Decimal(str(self.get_current_price(sym)))
        spend = Decimal(str(amount_usdt)) if amount_usdt else self.bot.current_cash

        if Trade.objects.filter(bot_config=self.bot, status='OPEN').exists():
            return {"ok": False, "error": f"Cannot SELL: Bot already holds an open position in {sym}! Use buyclose() or sellclose() first."}

        if not self.is_futures and self.is_authenticated:
            return {"ok": False, "error": "Cannot open SHORT in Spot mode. Set BINANCE_MARKET=futures."}

        if spend < Decimal("10.00"):
            return {"ok": False, "error": f"Cannot SELL: Spend amount (${spend:.2f}) is below Binance minimum $10 USDT!"}

        if spend > self.bot.current_cash:
            return {"ok": False, "error": f"Cannot SELL: Insufficient cash balance (${self.bot.current_cash:.2f} USDT)!"}

        fee = spend * self.bot.fee_pct
        investable = spend - fee
        raw_qty = investable / price
        quantity = self._round_quantity(sym, Decimal(str(raw_qty)))

        binance_order_id = None
        if self.is_authenticated:
            try:
                self.client.futures_change_leverage(symbol=sym, leverage=self.leverage)
            except Exception:
                pass
            res = self.client.futures_create_order(
                symbol=sym,
                side=Client.SIDE_SELL,
                type=Client.ORDER_TYPE_MARKET,
                quantity=float(quantity),
            )
            binance_order_id = res.get("orderId")
            if res.get("executedQty") and float(res["executedQty"]) > 0:
                quantity = Decimal(str(res["executedQty"]))
            if res.get("cummulativeQuoteQty") and float(res["cummulativeQuoteQty"]) > 0:
                spend = Decimal(str(res["cummulativeQuoteQty"]))
                price = spend / quantity if quantity else price

        trade = Trade.objects.create(
            bot_config=self.bot,
            symbol=sym,
            status='OPEN',
            side='SHORT',
            entry_time=timezone.now(),
            entry_price=price,
            quantity=quantity,
            cost=spend,
            notes=f"SHORT SELL Order {'(Binance #' + str(binance_order_id) + ')' if binance_order_id else '(Paper)'}"
        )

        self.bot.current_cash -= spend
        self.bot.save(update_fields=['current_cash', 'updated_at'])

        SignalLog.objects.create(
            bot_config=self.bot,
            timestamp=timezone.now(),
            symbol=sym,
            signal_type='SELL',
            price=price,
            fast_ema=Decimal("0"),
            slow_ema=Decimal("0"),
            executed=True,
            message=f"SHORT SELL executed: {quantity} {sym} @ ${price:.2f}"
        )

        return {
            "ok": True,
            "action": "SELL",
            "side": "SHORT",
            "trade_id": trade.id,
            "price": float(price),
            "quantity": float(quantity),
            "cost": float(spend),
            "remaining_cash": float(self.bot.current_cash),
            "binance_order_id": binance_order_id
        }

    # -------------------------------------------------------------------------
    # 6. SELLCLOSE: Close SHORT position (Buy back to cover)
    # -------------------------------------------------------------------------
    def sellclose(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Close an open SHORT position with a reduce-only BUY."""
        sym = symbol or self.bot.symbol
        price = Decimal(str(self.get_current_price(sym)))

        open_trade = Trade.objects.filter(bot_config=self.bot, status='OPEN').first()
        if not open_trade:
            return {"ok": False, "error": "Cannot SELLCLOSE: No open Short position currently exists to close!"}
        if open_trade.side != "SHORT":
            return {"ok": False, "error": "Cannot SELLCLOSE: Open position is LONG. Use buyclose() instead."}

        qty = open_trade.quantity
        entry_fee = open_trade.cost * self.bot.fee_pct
        exit_fee = qty * price * self.bot.fee_pct
        pnl = (open_trade.entry_price - price) * qty - entry_fee - exit_fee
        net_rev = open_trade.cost + pnl
        pnl_pct = (pnl / open_trade.cost) * Decimal("100.0") if open_trade.cost else Decimal("0.00")

        binance_order_id = None
        if self.is_authenticated:
            buy_qty = self._round_quantity(sym, qty)
            res = self.client.futures_create_order(
                symbol=sym,
                side=Client.SIDE_BUY,
                type=Client.ORDER_TYPE_MARKET,
                quantity=float(buy_qty),
                reduceOnly="true",
            )
            binance_order_id = res.get("orderId")
            if res.get("executedQty") and float(res["executedQty"]) > 0:
                qty = Decimal(str(res["executedQty"]))
            if res.get("cummulativeQuoteQty") and float(res["cummulativeQuoteQty"]) > 0:
                quote_qty = Decimal(str(res["cummulativeQuoteQty"]))
                price = quote_qty / qty if qty else price
                exit_fee = quote_qty * self.bot.fee_pct
                pnl = (open_trade.entry_price - price) * qty - entry_fee - exit_fee
                net_rev = open_trade.cost + pnl
                pnl_pct = (pnl / open_trade.cost) * Decimal("100.0") if open_trade.cost else Decimal("0.00")

        open_trade.status = 'CLOSED'
        open_trade.exit_time = timezone.now()
        open_trade.exit_price = price
        open_trade.net_revenue = net_rev
        open_trade.pnl = pnl
        open_trade.pnl_pct = pnl_pct
        open_trade.is_win = pnl > 0
        open_trade.notes += f" | SHORT SELLCLOSE @ ${price:.2f}"
        open_trade.save()

        self.bot.current_cash += net_rev
        self.bot.save(update_fields=['current_cash', 'updated_at'])

        SignalLog.objects.create(
            bot_config=self.bot,
            timestamp=timezone.now(),
            symbol=sym,
            signal_type='BUY',
            price=price,
            fast_ema=Decimal("0"),
            slow_ema=Decimal("0"),
            executed=True,
            message=f"SELLCLOSE executed: Realized PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
        )

        return {
            "ok": True,
            "action": "SELLCLOSE",
            "trade_id": open_trade.id,
            "exit_price": float(price),
            "quantity_bought": float(qty),
            "net_revenue": float(net_rev),
            "realized_pnl": float(pnl),
            "realized_pnl_pct": float(pnl_pct),
            "is_win": bool(pnl > 0),
            "new_cash_balance": float(self.bot.current_cash),
            "binance_order_id": binance_order_id
        }

    # -------------------------------------------------------------------------
    # 7. RESET: Clear trades and restore wallet for testing
    # -------------------------------------------------------------------------
    def reset(self) -> Dict[str, Any]:
        Trade.objects.filter(bot_config=self.bot).delete()
        SignalLog.objects.filter(bot_config=self.bot).delete()
        self.bot.current_cash = self.bot.initial_capital
        self.bot.save(update_fields=['current_cash', 'updated_at'])
        return {"ok": True, "cash": float(self.bot.current_cash)}
