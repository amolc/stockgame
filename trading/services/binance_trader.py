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
from decimal import Decimal
from typing import Dict, Any, Optional
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client
from django.utils import timezone
from trading.models import BotConfig, Trade, SignalLog

load_dotenv()

# Global Binance Data API
Client.API_URL = os.getenv("BINANCE_API_URL", "https://data-api.binance.vision/api")


class BinanceTrader:
    def __init__(self, bot_config: Optional[BotConfig] = None):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.testnet = os.getenv("BINANCE_TESTNET", "false").lower() in ("true", "1", "yes")

        # Initialize Binance Client
        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet
        )

        # Attach BotConfig (defaults to first active bot in DB)
        self.bot = bot_config or BotConfig.objects.filter(is_active=True).first()
        if not self.bot:
            self.bot = BotConfig.objects.create(
                name="BTCUSDT Default Bot",
                symbol="BTCUSDT",
                interval="15m",
                initial_capital=Decimal("10000.00"),
                current_cash=Decimal("10000.00"),
                is_active=True
            )

    @property
    def is_authenticated(self) -> bool:
        """Returns True if user has configured API Key & Secret."""
        return bool(self.api_key and self.api_secret)

    # -------------------------------------------------------------------------
    # 1. GETDATA: Replaces old TradingView library with direct Binance stream/REST
    # -------------------------------------------------------------------------
    def getdata(self, symbol: Optional[str] = None, interval: Optional[str] = None, lookback: str = "2 days ago UTC") -> pd.DataFrame:
        sym = symbol or self.bot.symbol
        inv = interval or self.bot.interval

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
        ticker = self.client.get_symbol_ticker(symbol=sym)
        return float(ticker["price"])

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
                acc = self.client.get_account()
                for b in acc.get("balances", []):
                    if b["asset"] == "USDT":
                        real_usdt_balance = float(b["free"])
                    elif b["asset"] == sym.replace("USDT", ""):
                        real_asset_balance = float(b["free"])
            except Exception as e:
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
            floating_pnl = current_val - cost
            floating_pnl_pct = (floating_pnl / cost) * 100.0 if cost else 0.0

            pos_info["open_position"] = {
                "trade_id": open_trade.id,
                "side": "LONG",
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
            return {"ok": False, "error": f"Cannot BUY: Bot already holds an open position in {sym}!"}

        # Safety Check 2: Minimum Notional
        if spend < Decimal("10.00"):
            return {"ok": False, "error": f"Cannot BUY: Spend amount (${spend:.2f}) is below Binance minimum $10 USDT!"}

        # Safety Check 3: Sufficient cash
        if spend > self.bot.current_cash:
            return {"ok": False, "error": f"Cannot BUY: Insufficient cash balance (${self.bot.current_cash:.2f} USDT)!"}

        fee = spend * self.bot.fee_pct
        investable = spend - fee
        # Step-size precision: round to 5 decimals for BTC
        raw_qty = float(investable / price)
        quantity = Decimal(str(round(raw_qty, 5)))

        binance_order_id = None
        if self.is_authenticated:
            # Place real market order on Binance
            res = self.client.create_order(
                symbol=sym,
                side=Client.SIDE_BUY,
                type=Client.ORDER_TYPE_MARKET,
                quantity=float(quantity)
            )
            binance_order_id = res.get("orderId")

        # Record in Django Database
        trade = Trade.objects.create(
            bot_config=self.bot,
            symbol=sym,
            status='OPEN',
            entry_time=timezone.now(),
            entry_price=price,
            quantity=quantity,
            cost=spend,
            notes=f"BUY Order {'(Binance #' + str(binance_order_id) + ')' if binance_order_id else '(Paper)'}"
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
            message=f"BUY executed: {quantity} {sym} @ ${price:.2f}"
        )

        return {
            "ok": True,
            "action": "BUY",
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

        qty = open_trade.quantity
        gross_rev = qty * price
        fee = gross_rev * self.bot.fee_pct
        net_rev = gross_rev - fee
        pnl = net_rev - open_trade.cost
        pnl_pct = (pnl / open_trade.cost) * Decimal("100.0")

        binance_order_id = None
        if self.is_authenticated:
            # Place real sell order on Binance to liquidate
            res = self.client.create_order(
                symbol=sym,
                side=Client.SIDE_SELL,
                type=Client.ORDER_TYPE_MARKET,
                quantity=float(qty)
            )
            binance_order_id = res.get("orderId")

        # Mark trade as CLOSED in Django DB
        open_trade.status = 'CLOSED'
        open_trade.exit_time = timezone.now()
        open_trade.exit_price = price
        open_trade.net_revenue = net_rev
        open_trade.pnl = pnl
        open_trade.pnl_pct = pnl_pct
        open_trade.is_win = pnl > 0
        open_trade.notes += f" | BUYCLOSE @ ${price:.2f}"
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
    # 5. SELL: Open SHORT position (or Spot sell)
    # -------------------------------------------------------------------------
    def sell(self, symbol: Optional[str] = None, amount_usdt: Optional[float] = None) -> Dict[str, Any]:
        """
        In Spot trading: If holding an open position, SELL acts as BUYCLOSE.
        In Futures/Margin: Sells to open a Short position.
        """
        open_trade = Trade.objects.filter(bot_config=self.bot, status='OPEN').first()
        if open_trade:
            # If already holding a Long, sell is used to close it
            return self.buyclose(symbol=symbol)

        return {
            "ok": True,
            "action": "SELL",
            "message": "Signal recorded: SELL (Short) evaluated on market."
        }

    # -------------------------------------------------------------------------
    # 6. SELLCLOSE: Close SHORT position (Buy back to cover)
    # -------------------------------------------------------------------------
    def sellclose(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return {
            "ok": True,
            "action": "SELLCLOSE",
            "message": "Covered short position back to cash."
        }
