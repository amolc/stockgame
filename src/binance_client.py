"""
Binance API Client Wrapper
Handles direct connection to Binance REST API for market data and optional order execution.
Supports both Free Public Mode (no keys) and Authenticated Mode (keys provided).
"""

import os
import pandas as pd
from typing import Optional
from dotenv import load_dotenv
from binance.client import Client

# Load environment variables if .env file exists
load_dotenv()


class BinanceDataClient:
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = False):
        self.api_key = api_key if api_key is not None else os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret if api_secret is not None else os.getenv("BINANCE_API_SECRET", "")
        
        env_testnet = os.getenv("BINANCE_TESTNET", "false").lower() in ("true", "1", "yes")
        self.testnet = testnet or env_testnet
        
        # Initialize client (works with empty strings for all public endpoints)
        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet
        )

    def is_authenticated(self) -> bool:
        """Returns True if user has configured API credentials."""
        return bool(self.api_key and self.api_secret)

    def get_current_price(self, symbol: str = "BTCUSDT") -> float:
        """Fetch the latest real-time ticker price for a symbol."""
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_klines(self, symbol: str = "BTCUSDT", interval: str = "15m", lookback: str = "2 days ago UTC") -> pd.DataFrame:
        """
        Fetch historical candlestick (kline) data directly from Binance.
        
        :param symbol: Trading pair (e.g., 'BTCUSDT', 'ETHUSDT')
        :param interval: Candle interval ('1m', '5m', '15m', '1h', '4h', '1d')
        :param lookback: Time horizon (e.g., '1 day ago UTC', '7 days ago UTC')
        :return: Clean Pandas DataFrame with OHLCV data and datetime index
        """
        raw_klines = self.client.get_historical_klines(
            symbol=symbol,
            interval=interval,
            start_str=lookback
        )
        
        if not raw_klines:
            raise ValueError(f"No kline data returned from Binance for {symbol} ({interval}, {lookback})")

        columns = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ]
        
        df = pd.DataFrame(raw_klines, columns=columns)
        
        # Keep OHLCV core columns
        df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
        
        # Convert timestamp from milliseconds to UTC datetime
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms")
        df.set_index("datetime", inplace=True)
        df.drop(columns=["open_time"], inplace=True)
        
        # Convert prices and volume from string to float
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
            
        return df
