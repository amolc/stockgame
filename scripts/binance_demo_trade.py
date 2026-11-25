"""
Binance Testnet Demo Trading Script: binance_demo_trade.py

Modular script to test each step of demo trading using Binance Testnet API keys.
Can be executed directly via Python CLI or through Django Extensions runscript:

Usage (Direct CLI):
  python scripts/binance_demo_trade.py check_connection
  python scripts/binance_demo_trade.py check_balances
  python scripts/binance_demo_trade.py get_symbol_rules
  python scripts/binance_demo_trade.py test_order_buy
  python scripts/binance_demo_trade.py demo_buy
  python scripts/binance_demo_trade.py demo_positions
  python scripts/binance_demo_trade.py demo_sell
  python scripts/binance_demo_trade.py all

Usage (Django Runscript):
  python manage.py runscript binance_demo_trade --script-args check_balances
"""

import os
import sys
from pathlib import Path

# Force UTF-8 stdout to avoid Windows charmap errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from decimal import Decimal, ROUND_DOWN
from typing import Dict, Any, Optional
import django
from dotenv import load_dotenv

# Ensure Django environment is configured
if not os.getenv("DJANGO_SETTINGS_MODULE"):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stockgame.settings")
    django.setup()

from binance.client import Client
from django.utils import timezone
from trading.models import BotConfig, Trade, SignalLog

load_dotenv()


def get_binance_testnet_client() -> Client:
    """
    Initializes and returns an authenticated Binance Client pointed to Binance Spot Testnet.
    """
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    testnet = os.getenv("BINANCE_TESTNET", "true").lower() in ("true", "1", "yes")

    if not api_key or not api_secret:
        raise ValueError("BINANCE_API_KEY or BINANCE_API_SECRET missing in .env file!")

    if testnet:
        Client.API_URL = Client.API_TESTNET_URL

    client = Client(
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet,
        ping=False,
        requests_params={"timeout": 25}
    )

    if testnet:
        # Explicitly ensure Spot Testnet URL is targeted
        client.API_URL = client.API_TESTNET_URL

    return client


def check_connection(client: Optional[Client] = None) -> bool:
    """
    1. Tests ping and server time to confirm connectivity to Binance Spot Testnet.
    """
    print("\n" + "=" * 60)
    print("ACTION 1: Testing Connection to Binance Spot Testnet")
    print("=" * 60)
    c = client or get_binance_testnet_client()
    try:
        ping = c.ping()
        server_time = c.get_server_time()
        print(f"  [OK] Ping status: {ping or 'Success (200 OK)'}")
        print(f"  [OK] Server Time (ms): {server_time.get('serverTime')}")
        print(f"  [OK] Connected endpoint: {c.API_URL}")
        return True
    except Exception as e:
        print(f"  [FAIL] Could not connect to Binance Testnet: {e}")
        return False


def check_balances(client: Optional[Client] = None) -> Dict[str, Any]:
    """
    2. Fetches account details and lists non-zero virtual asset balances (USDT, BTC, etc.).
    """
    print("\n" + "=" * 60)
    print("ACTION 2: Checking Spot Testnet Wallet Balances")
    print("=" * 60)
    c = client or get_binance_testnet_client()
    try:
        acc = c.get_account()
        can_trade = acc.get("canTrade", False)
        print(f"  [OK] Account Spot Trading Permission: {'ENABLED' if can_trade else 'DISABLED'}")
        print("  [*] Active Asset Balances:")

        non_zero = []
        for b in acc.get("balances", []):
            free = float(b["free"])
            locked = float(b["locked"])
            if free > 0 or locked > 0:
                non_zero.append(b)
                print(f"      - {b['asset']:<8} Free: {free:,.6f} | Locked: {locked:,.6f}")

        if not non_zero:
            print("      (No assets with positive balance found in testnet account)")

        return acc
    except Exception as e:
        print(f"  [FAIL] Failed to retrieve testnet account balances: {e}")
        return {}


def get_symbol_rules(symbol: str = "BTCUSDT", client: Optional[Client] = None) -> Dict[str, Any]:
    """
    3. Fetches live price and trading rules (lot size, step size, min notional) for the symbol.
    """
    print("\n" + "=" * 60)
    print(f"ACTION 3: Querying Market Rules for {symbol}")
    print("=" * 60)
    c = client or get_binance_testnet_client()
    try:
        ticker = c.get_symbol_ticker(symbol=symbol)
        price = float(ticker["price"])
        print(f"  [OK] Live {symbol} Testnet Price: ${price:,.2f}")

        info = c.get_symbol_info(symbol=symbol)
        filters = {f["filterType"]: f for f in info.get("filters", [])}

        lot_filter = filters.get("LOT_SIZE", {})
        min_qty = float(lot_filter.get("minQty", 0.00001))
        step_size = float(lot_filter.get("stepSize", 0.00001))

        notional_filter = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
        min_notional = float(notional_filter.get("minNotional", 10.0))

        print(f"  [OK] Min Order Quantity:   {min_qty} BTC")
        print(f"  [OK] Step Size (Precision): {step_size} BTC")
        print(f"  [OK] Minimum Order Value:  ${min_notional:.2f} USDT")

        return {
            "symbol": symbol,
            "price": price,
            "min_qty": min_qty,
            "step_size": step_size,
            "min_notional": min_notional
        }
    except Exception as e:
        print(f"  [FAIL] Failed to fetch symbol rules: {e}")
        return {}


def test_order_buy(symbol: str = "BTCUSDT", usdt_amount: float = 50.0, client: Optional[Client] = None) -> bool:
    """
    4. Calls Binance `create_test_order()` to validate order syntax, lot size, and signatures
       without executing or altering account balances.
    """
    print("\n" + "=" * 60)
    print(f"ACTION 4: Validating Test Order (Zero-Risk create_test_order) for ${usdt_amount:.2f} USDT")
    print("=" * 60)
    c = client or get_binance_testnet_client()
    try:
        rules = get_symbol_rules(symbol=symbol, client=c)
        price = rules.get("price")
        step_size = rules.get("step_size", 0.00001)

        raw_qty = usdt_amount / price
        # Round quantity down according to step size precision
        precision = 5 if step_size <= 0.00001 else 3
        qty = round(raw_qty, precision)

        print(f"  [*] Calculated Testnet Order: BUY {qty} {symbol} (~${qty * price:.2f} USDT)")

        # create_test_order sends full payload to matching engine for validation only
        res = c.create_test_order(
            symbol=symbol,
            side=Client.SIDE_BUY,
            type=Client.ORDER_TYPE_MARKET,
            quantity=qty
        )
        print(f"  [OK] create_test_order passed! Binance accepted payload syntax: {res or '{}'}")
        print("  [OK] Your API Key, permissions, and parameters are fully validated.")
        return True
    except Exception as e:
        print(f"  [FAIL] create_test_order failed: {e}")
        return False


def demo_buy(symbol: str = "BTCUSDT", usdt_amount: float = 50.0, client: Optional[Client] = None) -> Dict[str, Any]:
    """
    5. Executes a REAL demo market BUY order on Binance Spot Testnet,
       and records the position into Django database.
    """
    print("\n" + "=" * 60)
    print(f"ACTION 5: Executing Demo Market BUY Order for ${usdt_amount:.2f} USDT")
    print("=" * 60)
    c = client or get_binance_testnet_client()

    # Get or create active BotConfig
    bot = BotConfig.objects.filter(is_active=True).first()
    if not bot:
        bot = BotConfig.objects.create(
            name="BTCUSDT Testnet Bot",
            symbol=symbol,
            interval="15m",
            initial_capital=Decimal("1000.00"),
            current_cash=Decimal("1000.00"),
            is_active=True,
            mode='LIVE'
        )

    # Check if position already open
    open_trade = Trade.objects.filter(bot_config=bot, status='OPEN').first()
    if open_trade:
        print(f"  [BLOCKED] Already holding open position: Trade #{open_trade.id} ({open_trade.quantity} BTC)")
        return {"ok": False, "error": "Open position already exists"}

    try:
        rules = get_symbol_rules(symbol=symbol, client=c)
        price = Decimal(str(rules["price"]))
        spend = Decimal(str(usdt_amount))

        precision = 5
        raw_qty = float(spend / price)
        qty = round(raw_qty, precision)

        print(f"  [*] Sending Market BUY to Binance Testnet: {qty} {symbol}...")
        order_res = c.create_order(
            symbol=symbol,
            side=Client.SIDE_BUY,
            type=Client.ORDER_TYPE_MARKET,
            quantity=qty
        )

        order_id = order_res.get("orderId")
        executed_qty = Decimal(str(order_res.get("executedQty", qty)))
        cummulative_quote = Decimal(str(order_res.get("cummulativeQuoteQty", spend)))
        fill_price = cummulative_quote / executed_qty if executed_qty > 0 else price

        print(f"  [OK] ORDER FILLED ON BINANCE TESTNET!")
        print(f"       Order ID:    {order_id}")
        print(f"       Status:      {order_res.get('status')}")
        print(f"       Filled Qty:  {executed_qty} BTC")
        print(f"       Avg Price:   ${fill_price:,.2f}")
        print(f"       Total Cost:  ${cummulative_quote:,.2f} USDT")

        # Record in Django Database
        trade = Trade.objects.create(
            bot_config=bot,
            symbol=symbol,
            status='OPEN',
            entry_time=timezone.now(),
            entry_price=fill_price,
            quantity=executed_qty,
            cost=cummulative_quote,
            notes=f"Testnet BUY Order #{order_id}"
        )

        SignalLog.objects.create(
            bot_config=bot,
            timestamp=timezone.now(),
            symbol=symbol,
            signal_type='BUY',
            price=fill_price,
            fast_ema=Decimal("0"),
            slow_ema=Decimal("0"),
            executed=True,
            message=f"Demo BUY filled on Binance Testnet: #{order_id}"
        )

        return {"ok": True, "order_id": order_id, "trade_id": trade.id, "qty": float(executed_qty), "price": float(fill_price)}

    except Exception as e:
        print(f"  [FAIL] Failed to execute Testnet BUY: {e}")
        return {"ok": False, "error": str(e)}


def demo_positions(symbol: str = "BTCUSDT", client: Optional[Client] = None) -> Dict[str, Any]:
    """
    6. Checks both the local Django database position and the live Binance Testnet wallet.
    """
    print("\n" + "=" * 60)
    print(f"ACTION 6: Inspecting Active Position for {symbol}")
    print("=" * 60)
    c = client or get_binance_testnet_client()

    bot = BotConfig.objects.filter(is_active=True).first()
    open_trade = Trade.objects.filter(bot_config=bot, status='OPEN').first() if bot else None

    ticker = c.get_symbol_ticker(symbol=symbol)
    live_price = Decimal(str(ticker["price"]))

    if open_trade:
        qty = open_trade.quantity
        cost = open_trade.cost
        current_val = qty * live_price
        pnl = current_val - cost
        pnl_pct = (pnl / cost) * Decimal("100.0") if cost > 0 else Decimal("0.0")

        print(f"  [ACTIVE LONG POSITION FOUND]")
        print(f"    Trade ID:       #{open_trade.id}")
        print(f"    Entry Price:    ${open_trade.entry_price:,.2f}")
        print(f"    Current Price:  ${live_price:,.2f}")
        print(f"    Quantity:       {qty} BTC")
        print(f"    Invested Cost:  ${cost:,.2f} USDT")
        print(f"    Current Value:  ${current_val:,.2f} USDT")
        print(f"    Unrealized PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%)")
        return {"has_position": True, "trade_id": open_trade.id, "pnl": float(pnl), "pnl_pct": float(pnl_pct)}
    else:
        print("  [INFO] No active open position in database (Flat / 100% Cash).")
        return {"has_position": False}


def demo_sell(symbol: str = "BTCUSDT", client: Optional[Client] = None) -> Dict[str, Any]:
    """
    7. Executes a REAL demo market SELL order on Binance Spot Testnet,
       liquidating the position and recording realized PnL in Django.
    """
    print("\n" + "=" * 60)
    print(f"ACTION 7: Executing Demo Market SELL Order (Close Position)")
    print("=" * 60)
    c = client or get_binance_testnet_client()

    bot = BotConfig.objects.filter(is_active=True).first()
    open_trade = Trade.objects.filter(bot_config=bot, status='OPEN').first() if bot else None

    if not open_trade:
        print("  [BLOCKED] No open position currently exists to close.")
        return {"ok": False, "error": "No open position"}

    try:
        qty = float(open_trade.quantity)
        print(f"  [*] Sending Market SELL to Binance Testnet for {qty} BTC...")

        order_res = c.create_order(
            symbol=symbol,
            side=Client.SIDE_SELL,
            type=Client.ORDER_TYPE_MARKET,
            quantity=qty
        )

        order_id = order_res.get("orderId")
        cummulative_quote = Decimal(str(order_res.get("cummulativeQuoteQty", "0")))
        executed_qty = Decimal(str(order_res.get("executedQty", qty)))
        exit_price = cummulative_quote / executed_qty if executed_qty > 0 else Decimal("0")

        net_rev = cummulative_quote
        pnl = net_rev - open_trade.cost
        pnl_pct = (pnl / open_trade.cost) * Decimal("100.0") if open_trade.cost > 0 else Decimal("0")

        print(f"  [OK] SELL ORDER FILLED ON BINANCE TESTNET!")
        print(f"       Order ID:     {order_id}")
        print(f"       Sold Qty:     {executed_qty} BTC")
        print(f"       Exit Price:   ${exit_price:,.2f}")
        print(f"       Total Net:    ${net_rev:,.2f} USDT")
        print(f"       Realized PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%)")

        open_trade.status = 'CLOSED'
        open_trade.exit_time = timezone.now()
        open_trade.exit_price = exit_price
        open_trade.net_revenue = net_rev
        open_trade.pnl = pnl
        open_trade.pnl_pct = pnl_pct
        open_trade.is_win = pnl > 0
        open_trade.notes += f" | Testnet SELL Order #{order_id}"
        open_trade.save()

        SignalLog.objects.create(
            bot_config=bot,
            timestamp=timezone.now(),
            symbol=symbol,
            signal_type='SELL',
            price=exit_price,
            fast_ema=Decimal("0"),
            slow_ema=Decimal("0"),
            executed=True,
            message=f"Demo SELL filled on Binance Testnet: #{order_id} | PnL: ${pnl:+,.2f}"
        )

        return {"ok": True, "order_id": order_id, "pnl": float(pnl), "pnl_pct": float(pnl_pct)}

    except Exception as e:
        print(f"  [FAIL] Failed to execute Testnet SELL: {e}")
        return {"ok": False, "error": str(e)}


def run(*args):
    """
    Entry point for Django runscript or CLI runner.
    """
    action = args[0].lower() if args else "check_connection"

    dispatch = {
        "check_connection": check_connection,
        "check_balances": check_balances,
        "get_symbol_rules": get_symbol_rules,
        "test_order_buy": test_order_buy,
        "demo_buy": demo_buy,
        "demo_positions": demo_positions,
        "demo_sell": demo_sell,
    }

    if action in dispatch:
        dispatch[action]()
    elif action == "all":
        print("\n[*] Running Complete Testnet Verification Sequence...")
        if check_connection():
            check_balances()
            get_symbol_rules()
            test_order_buy()
    else:
        print(f"[!] Unknown action: '{action}'")
        print(f"    Available: {', '.join(dispatch.keys())}, all")


if __name__ == "__main__":
    action_arg = sys.argv[1] if len(sys.argv) > 1 else "check_connection"
    run(action_arg)
