import os
import time
from decimal import Decimal, ROUND_DOWN
from typing import Optional
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

_EXCHANGE_INFO_CACHE: dict[tuple[bool, bool, str], tuple[float, dict]] = {}
_EXCHANGE_INFO_TTL_SECONDS = 300.0


def _env_true(name: str) -> bool:
    return os.getenv(name, "false").lower() in ("true", "1", "yes")


class BinanceService:
    """
    Binance USDⓈ-M Futures / Spot client.

    Can be initialized from a TradingAccount record via BinanceService.from_account(account)
    or from environment variables as a fallback.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: Optional[bool] = None,
        demo: Optional[bool] = None,
        market: Optional[str] = None,
        leverage: Optional[int] = None,
        allow_env_credentials: bool = True,
    ):
        if allow_env_credentials:
            self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
            self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        else:
            self.api_key = api_key or ""
            self.api_secret = api_secret or ""

        self.demo = _env_true("BINANCE_DEMO") if demo is None else bool(demo)
        env_testnet = _env_true("BINANCE_TESTNET") if testnet is None else bool(testnet)
        self.testnet = env_testnet and not self.demo
        self.market = (market or os.getenv("BINANCE_MARKET", "futures")).lower()
        if leverage is not None:
            self.leverage = int(leverage)
        else:
            try:
                self.leverage = int(os.getenv("BINANCE_FUTURES_LEVERAGE", "5"))
            except ValueError:
                self.leverage = 5

        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet,
            demo=self.demo,
            ping=False,
            requests_params={"timeout": 25},
        )

    @classmethod
    def from_account(cls, account) -> "BinanceService":
        """Build a BinanceService bound to a specific TradingAccount."""
        env = getattr(account, "environment", "PAPER")
        is_paper = env == "PAPER"
        return cls(
            api_key="" if is_paper else (account.api_key or ""),
            api_secret="" if is_paper else account.get_api_secret(),
            demo=(env == "DEMO"),
            testnet=(env == "TESTNET"),
            market=getattr(account, "market", "futures"),
            leverage=getattr(account, "leverage", 5),
            allow_env_credentials=False,
        )

    @classmethod
    def for_bot(cls, bot_config) -> "BinanceService":
        """Build a BinanceService for a BotConfig, using its TradingAccount if attached."""
        account = getattr(bot_config, "account", None)
        if account is not None:
            return cls.from_account(account)
        return cls()

    @property
    def is_authenticated(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def is_futures(self) -> bool:
        return self.market == "futures"

    @property
    def is_demo(self) -> bool:
        """True when orders target a demo/testnet account instead of mainnet."""
        return self.demo or self.testnet

    def test_connection(self) -> tuple[bool, str, Optional[float]]:
        """
        Verify credentials and exchange connectivity for this client.
        Returns (ok, message, usdt_balance).
        """
        if not self.is_authenticated:
            return False, "API Key or Secret is missing (Paper mode only).", None

        try:
            if self.is_futures:
                balances = self.client.futures_account_balance()
                usdt_bal = 0.0
                for b in balances:
                    if b.get("asset") == "USDT":
                        usdt_bal = float(b.get("availableBalance", b.get("balance", 0)))
                        break
                env_label = "Demo Futures" if self.demo else ("Futures Testnet" if self.testnet else "Live Futures")
                return True, f"{env_label} connected. Available USDT: ${usdt_bal:,.2f}", usdt_bal

            acc = self.client.get_account()
            usdt_bal = 0.0
            for b in acc.get("balances", []):
                if b.get("asset") == "USDT":
                    usdt_bal = float(b.get("free", 0))
                    break
            env_label = "Spot Demo" if self.demo else ("Spot Testnet" if self.testnet else "Live Spot")
            return True, f"{env_label} connected. Free USDT: ${usdt_bal:,.2f}", usdt_bal
        except Exception as exc:
            return False, str(exc), None

    def fetch_klines(self, symbol: str = "BTCUSDT", interval: str = "15m", lookback: str = "3 days ago UTC") -> pd.DataFrame:
        if self.is_futures:
            # Futures klines ignore start_str; request the most recent window.
            raw_klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=1500)
        else:
            raw_klines = self.client.get_historical_klines(
                symbol=symbol,
                interval=interval,
                start_str=lookback,
            )
        if not raw_klines:
            raise ValueError(f"No kline data returned from Binance for {symbol} ({interval})")

        columns = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
        ]
        df = pd.DataFrame(raw_klines, columns=columns)
        df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()

        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        df.drop(columns=["open_time"], inplace=True)

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        return df

    def get_ticker_price(self, symbol: str = "BTCUSDT") -> float:
        if self.is_futures:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
        else:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_account_balances(self, symbol: str = "BTCUSDT") -> dict:
        """Fetch free USDT and open position size for the symbol when API keys are configured."""
        if not self.is_authenticated:
            return {"usdt": None, "asset": None}

        if self.is_futures:
            usdt_bal = None
            asset_bal = None
            for b in self.client.futures_account_balance():
                if b.get("asset") == "USDT":
                    usdt_bal = float(b.get("availableBalance", b.get("balance", 0)))
                    break
            try:
                for p in self.client.futures_position_information(symbol=symbol):
                    amt = float(p.get("positionAmt", 0))
                    if amt:
                        asset_bal = abs(amt)
                        break
            except Exception:
                pass
            return {"usdt": usdt_bal, "asset": asset_bal}

        base_asset = symbol[:-4] if symbol.endswith("USDT") else symbol
        acc = self.client.get_account()
        usdt_bal = None
        asset_bal = None
        for b in acc.get("balances", []):
            if b["asset"] == "USDT":
                usdt_bal = float(b["free"])
            elif b["asset"] == base_asset:
                asset_bal = float(b["free"])
        return {"usdt": usdt_bal, "asset": asset_bal}

    def _get_cached_futures_exchange_info(self) -> dict:
        cache_key = (self.demo, self.testnet, self.market)
        now = time.monotonic()
        cached = _EXCHANGE_INFO_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _EXCHANGE_INFO_TTL_SECONDS:
            return cached[1]
        info = self.client.futures_exchange_info()
        _EXCHANGE_INFO_CACHE[cache_key] = (now, info)
        return info

    def round_quantity(self, symbol: str, raw_qty: Decimal) -> Decimal:
        """Floor quantity to the symbol's Binance LOT_SIZE stepSize (falls back to 5 decimals)."""
        try:
            if self.is_futures:
                info = self._get_cached_futures_exchange_info()
                for s in info.get("symbols", []):
                    if s.get("symbol") == symbol:
                        for f in s.get("filters", []):
                            if f.get("filterType") == "LOT_SIZE":
                                step = Decimal(str(f["stepSize"])).normalize()
                                return (raw_qty / step).to_integral_value(rounding=ROUND_DOWN) * step
            else:
                info = self.client.get_symbol_info(symbol)
                if info:
                    for f in info.get("filters", []):
                        if f.get("filterType") == "LOT_SIZE":
                            step = Decimal(str(f["stepSize"])).normalize()
                            return (raw_qty / step).to_integral_value(rounding=ROUND_DOWN) * step
        except Exception:
            pass
        return raw_qty.quantize(Decimal("0.00001"), rounding=ROUND_DOWN)

    def set_leverage(self, symbol: str = "BTCUSDT") -> Optional[dict]:
        """Set configured futures leverage (no-op for spot or unauthenticated clients)."""
        if self.is_futures and self.is_authenticated:
            try:
                return self.client.futures_change_leverage(symbol=symbol, leverage=self.leverage)
            except Exception:
                return None
        return None

    def place_market_order(self, symbol: str, side: str, quantity: Decimal, reduce_only: bool = False) -> dict:
        """Execute a market order (futures or spot, depending on market setting)."""
        if self.is_futures:
            params = {
                "symbol": symbol,
                "side": side,
                "type": Client.ORDER_TYPE_MARKET,
                "quantity": float(quantity),
            }
            if reduce_only:
                params["reduceOnly"] = "true"
            return self.client.futures_create_order(**params)

        return self.client.create_order(
            symbol=symbol,
            side=side,
            type=Client.ORDER_TYPE_MARKET,
            quantity=float(quantity),
        )
