"""
Binance API Client Wrapper
Handles direct connection to Binance REST API for market data and optional order execution.
Supports Demo Trading (demo.binance.com keys), Testnet, and live modes, Spot or Futures.
"""

import os
import pandas as pd
from typing import Optional
from dotenv import load_dotenv
from binance.client import Client

# Load environment variables if .env file exists
load_dotenv()


def _env_true(name: str) -> bool:
    return os.getenv(name, "false").lower() in ("true", "1", "yes")


class BinanceDataClient:
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = False):
        self.api_key = api_key if api_key is not None else os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret if api_secret is not None else os.getenv("BINANCE_API_SECRET", "")

        self.demo = _env_true("BINANCE_DEMO")
        self.testnet = (testnet or _env_true("BINANCE_TESTNET")) and not self.demo
        self.market = os.getenv("BINANCE_MARKET", "futures").lower()

        # Initialize client (works with empty strings for all public endpoints)
        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet,
            demo=self.demo,
        )

    @property
    def is_futures(self) -> bool:
        return self.market == "futures"

    def is_authenticated(self) -> bool:
        """Returns True if user has configured API credentials."""
        return bool(self.api_key and self.api_secret)

    def get_current_price(self, symbol: str = "BTCUSDT") -> float:
        """Fetch the latest real-time ticker price for a symbol."""
        if self.is_futures:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
        else:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_klines(self, symbol: str = "BTCUSDT", interval: str = "15m", lookback: str = "2 days ago UTC") -> pd.DataFrame:
        if self.is_futures:
            raw_klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=1500)
        else:
            raw_klines = self.client.get_historical_klines(
                symbol=symbol,
                interval=interval,
                start_str=lookback,
            )

        if not raw_klines:
            raise ValueError(f"No kline data returned from Binance for {symbol} ({interval}, {lookback})")

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
