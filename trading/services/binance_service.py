import os
from decimal import Decimal, ROUND_DOWN
from typing import Optional
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

# Point to Binance's dedicated global public data cluster (bypasses ISP timeouts)
Client.API_URL = os.getenv("BINANCE_API_URL", "https://data-api.binance.vision/api")


class BinanceService:
    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False):
        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        self.testnet = testnet or (os.getenv("BINANCE_TESTNET", "false").lower() in ("true", "1", "yes"))

        if self.testnet:
            Client.API_URL = Client.API_TESTNET_URL

        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet,
            ping=False,
            requests_params={"timeout": 25}
        )
        if self.testnet:
            self.client.API_URL = self.client.API_TESTNET_URL

    @property
    def is_authenticated(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def fetch_klines(self, symbol: str = "BTCUSDT", interval: str = "15m", lookback: str = "3 days ago UTC") -> pd.DataFrame:
        raw_klines = self.client.get_historical_klines(
            symbol=symbol,
            interval=interval,
            start_str=lookback
        )
        if not raw_klines:
            raise ValueError(f"No kline data returned from Binance for {symbol} ({interval})")

        columns = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
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
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_account_balances(self, symbol: str = "BTCUSDT") -> dict:
        """Fetch free USDT and base-asset balances when API keys are configured."""
        if not self.is_authenticated:
            return {"usdt": None, "asset": None}
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

    def round_quantity(self, symbol: str, raw_qty: Decimal) -> Decimal:
        """Floor quantity to the symbol's Binance LOT_SIZE stepSize (falls back to 5 decimals)."""
        try:
            info = self.client.get_symbol_info(symbol)
            if info:
                for f in info.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        step = Decimal(str(f["stepSize"])).normalize()
                        return (raw_qty / step).to_integral_value(rounding=ROUND_DOWN) * step
        except Exception:
            pass
        return raw_qty.quantize(Decimal("0.00001"), rounding=ROUND_DOWN)

    def place_market_order(self, symbol: str, side: str, quantity: Decimal) -> dict:
        """Execute a spot market order on Binance (Testnet or Live depending on client URL)."""
        return self.client.create_order(
            symbol=symbol,
            side=side,
            type=Client.ORDER_TYPE_MARKET,
            quantity=float(quantity),
        )
